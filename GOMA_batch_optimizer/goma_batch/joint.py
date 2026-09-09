"""Direct joint MIQCP. Builds one original GOMA block per concurrent slot.

Copies original linear/quadratic/indicator rows using documented Gurobi APIs,
not source rewriting or monkey-patching. Only the fixed PE equality is replaced.
"""
from __future__ import annotations
import math
import time
from pathlib import Path
from .core import Problem, Profile, schedule
from .upstream import PARAMS, extract_mapping, set_start


def _copy_block(source, target, prefix):
    import gurobipy as gp
    from gurobipy import GRB
    source.update()
    if source.NumSOS or source.NumPWLObjVars or source.NumObj != 1:
        raise RuntimeError('Unsupported upstream SOS/PWL/multiobjective model')
    original=source.getVars()
    vs={v.index:target.addVar(lb=v.LB,ub=v.UB,vtype=v.VType,name=prefix+v.VarName) for v in original}
    def lin(ex):
        out=gp.LinExpr(ex.getConstant())
        for k in range(ex.size()): out.addTerms(ex.getCoeff(k),vs[ex.getVar(k).index])
        return out
    def quad(ex):
        out=gp.QuadExpr(lin(ex.getLinExpr()))
        for k in range(ex.size()):
            out.addTerms(ex.getCoeff(k),vs[ex.getVar1(k).index],vs[ex.getVar2(k).index])
        return out
    for c in source.getConstrs():
        target.addLConstr(lin(source.getRow(c)),c.Sense,c.RHS,name=prefix+c.ConstrName)
    skipped=0
    for c in source.getQConstrs():
        if c.QCName == 'pe_product_xyz': skipped+=1; continue
        target.addQConstr(quad(source.getQCRow(c)),c.QCSense,c.QCRHS,name=prefix+c.QCName)
    if skipped != 1: raise RuntimeError('Expected exactly one upstream PE product equality')
    for c in source.getGenConstrs():
        if c.GenConstrType != GRB.GENCONSTR_INDICATOR:
            raise RuntimeError('Unsupported upstream general-constraint type')
        bv,bvalue,ex,sense,rhs=source.getGenConstrIndicator(c)
        target.addGenConstrIndicator(vs[bv.index],bvalue,lin(ex),sense,rhs,name=prefix+c.GenConstrName)
    obj=source.getObjective()
    if isinstance(obj,gp.QuadExpr): raise RuntimeError('Expected a linear upstream energy objective')
    return {v.VarName:vs[v.index] for v in original},lin(obj)


def build_joint(problem: Problem, upstream, q: int, *, params=None, pe_domain=True,
                footprint_symmetry=True):
    import gurobipy as gp
    from gurobipy import GRB
    if q not in problem.qs: raise ValueError('q is not a permitted periodic concurrency')
    par=dict(PARAMS);par.update(params or {})
    par.update(NonConvex=2,MIPGap=0.0,MIPGapAbs=0.0)
    source=upstream.full.build_model_full(problem.cfg,par)[0]
    model=gp.Model(f'goma_batch_joint_q{q}')
    for k,v in par.items(): model.setParam(k,v)
    blocks=[]; ns=[]; footprints=[]; es=[]; first_selector=None
    scale=max(1.0,math.sqrt(problem.C))
    legal=problem.ns
    try:
        for b in range(q):
            vs,e=_copy_block(source,model,f'b{b}__')
            n=model.addVar(vtype=GRB.INTEGER,lb=1,ub=min(problem.V,problem.N-q+1),name=f'n_{b}')
            model.addQConstr(vs['product_k2_xy']*vs['k_2_z'] == n,name=f'pe_{b}')
            if pe_domain or (b==0 and (problem.objective=='edp' or problem.power)):
                upper_n=min(problem.V,problem.N//q if b==0 else problem.N-q+1)
                ds=[t for t in legal if t <= upper_n]
                choose=[model.addVar(vtype=GRB.BINARY,name=f'nsel_{b}_{t}') for t in ds]
                model.addConstr(gp.quicksum(choose)==1,name=f'nsel_one_{b}')
                model.addConstr(n==gp.quicksum(t*v for t,v in zip(ds,choose)),name=f'nsel_value_{b}')
                if b==0: first_selector=(ds,choose)
            occupied=gp.quicksum(vs[f'occupied_scaled_1_{d}'] for d in ('x','y','z'))
            model.addConstr(e >= problem.io_bound,name=f'io_lower_{b}')
            blocks.append(vs);ns.append(n);footprints.append(occupied);es.append(e)
        model.addConstr(gp.quicksum(ns)==problem.N,name='all_PE_allocated')
        model.addConstr(gp.quicksum(footprints)<=problem.C/scale,name='shared_SRAM')
        for b in range(q-1):
            model.addConstr(ns[b]<=ns[b+1],name=f'ordered_pe_{b}')
            if footprint_symmetry:
                same=model.addVar(vtype=GRB.BINARY,name=f'equal_pe_{b}')
                model.addGenConstrIndicator(same,1,ns[b+1]==ns[b],name=f'equal_pe_on_{b}')
                model.addGenConstrIndicator(same,0,ns[b+1]>=ns[b]+1,name=f'equal_pe_off_{b}')
                model.addGenConstrIndicator(same,1,footprints[b]<=footprints[b+1],name=f'ordered_footprint_{b}')
        # Sorting makes n_0 the actual minimum. Lookup its finite divisor
        # domain and use affine indicator epigraphs: EDP introduces NO new
        # bilinear energy/time coupling and no reciprocal nonlinear equality.
        S=gp.quicksum(es)
        if problem.objective == 'energy':
            leak=0.0
            if problem.power:
                ds,choose=first_selector
                leak=problem.power*gp.quicksum(v/t for t,v in zip(ds,choose))
            obj=(S+leak)/q
        else:
            # A loose finite upper bound valid for every feasible mapping.
            # Each path/axis has source + receiver weights bounded by twice
            # the sum of all six access costs; there are fewer than 18 such
            # contributions. This bound only tightens the epigraph variable.
            six=sum(problem.cfg[k] for k in ('E_DDR_r','E_DDR_w','E_SRAM_r',
                                            'E_SRAM_w','E_RF_r','E_RF_w'))
            phi_upper=problem.cfg['E_MACC']+18*six
            obj=model.addVar(lb=problem.universal_bound(),
                             ub=problem.N*phi_upper/q+problem.N*problem.power/(q*q),
                             name='normalized_workload_edp')
            ds,choose=first_selector
            for s,selector in zip(ds,choose):
                rhs=(problem.N/(q*q*s))*S+problem.N*problem.power/(q*q*s*s)
                model.addGenConstrIndicator(selector,1,obj>=rhs,name=f'workload_edp_if_min_{s}')
        model.setObjective(obj,GRB.MINIMIZE)
        model.update()
        return model,blocks,ns
    except Exception:
        model.dispose();raise
    finally:
        source.dispose()


def solve_joint_q(problem: Problem, upstream, q: int, seconds: float, *, params=None,
                  incumbent=None, export_dir=None, pe_domain=True) -> dict:
    import gurobipy as gp
    from gurobipy import GRB
    start=time.monotonic()
    model,blocks,ns=build_joint(problem,upstream,q,params=params,pe_domain=pe_domain)
    try:
        if incumbent and incumbent['q']==q:
            ps=[]
            for g in incumbent['allocation_groups']:
                prof=upstream.evaluate(problem,g['mapping'],n=g['n'])
                ps.extend([prof]*g['multiplicity'])
            for i,p in enumerate(sorted(ps,key=lambda p:(p.n,p.c))):
                set_start(blocks[i],p,problem);ns[i].Start=p.n
        if export_dir:
            dest=Path(export_dir);dest.mkdir(parents=True,exist_ok=True)
            model.write(str(dest/f'joint_q{q}.lp'))
            model.Params.LogFile=str(dest/f'joint_q{q}.log')
        model.Params.TimeLimit=max(0.001,seconds-(time.monotonic()-start))
        model.optimize()
        if model.Status==GRB.INFEASIBLE:
            return dict(status='INFEASIBLE',lower=math.inf,profiles=[],counts={},q=q)
        if model.Status in (GRB.INF_OR_UNBD,GRB.UNBOUNDED,GRB.NUMERIC):
            raise RuntimeError(f'Unexpected joint solver status {model.Status}')
        finite=[problem.score(q,s,q*problem.io_bound) for qq,s in problem.pairs() if qq==q]
        raw_bound=float(model.ObjBound)
        lower=max(min(finite,default=math.inf),raw_bound if math.isfinite(raw_bound) else -math.inf)
        profiles=[];counts={};metrics=None
        if model.SolCount:
            for vs in blocks:
                prof=upstream.evaluate(problem,extract_mapping(vs))
                profiles.append(prof);counts[prof.id]=counts.get(prof.id,0)+1
            metrics=schedule(problem,profiles,counts)
            exact=metrics['normalized_objective']
            if not math.isclose(exact,float(model.ObjVal),rel_tol=1e-7,abs_tol=1e-8):
                raise RuntimeError('Joint objective disagrees with the original analytical evaluator')
            if lower>exact+max(1e-8,abs(exact)*1e-7): raise RuntimeError('Invalid joint lower bound')
            lower=min(lower,exact)
            if export_dir: model.write(str(Path(export_dir)/f'joint_q{q}.sol'))
        return dict(status=str(model.Status),q=q,lower=lower,profiles=profiles,counts=counts,
                    schedule=metrics,runtime=time.monotonic()-start,variables=model.NumVars,
                    constraints=model.NumConstrs,quadratic_constraints=model.NumQConstrs,
                    indicator_constraints=model.NumGenConstrs,
                    raw_objective=float(model.ObjVal) if model.SolCount else None,
                    raw_bound=raw_bound,solver_gap=float(model.MIPGap) if model.SolCount else None)
    finally:
        model.dispose()
