from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import time
from .core import Problem, Budget, gap, closed_gap, schedule, Option, ENERGY_KEYS
from .master import solve_master
from .adaptive import solve_adaptive
from .upstream import Upstream, GurobiOracle, COMMIT
from .joint import solve_joint_q


def json_safe(x):
    if isinstance(x,float) and not math.isfinite(x): return None
    if isinstance(x,dict): return {str(k):json_safe(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)): return [json_safe(v) for v in x]
    return x


def main(argv=None):
    ap=argparse.ArgumentParser(description='GOMA batch resource allocation and mapping optimization')
    ap.add_argument('input',type=Path,help='JSON containing cfg, B, objective and leakage')
    ap.add_argument('--goma-root',type=Path,required=True,help='Checkout with the two main-root GOMA models')
    ap.add_argument('--method',choices=['hybrid','adaptive','joint'],default='hybrid')
    ap.add_argument('--seconds',type=float,default=300)
    ap.add_argument('--oracle-seconds',type=float,default=30)
    ap.add_argument('--master-seconds',type=float,default=10)
    ap.add_argument('--master-backend',choices=['auto','gurobi','scipy'],default='scipy')
    ap.add_argument('--relative-gap',type=float,default=1e-6)
    ap.add_argument('--absolute-gap',type=float,default=1e-9,help='Absolute gap in normalized objective units')
    ap.add_argument('--threads',type=int,default=0)
    ap.add_argument('--joint-slots',type=int,default=8,help='Largest q used by hybrid joint probes')
    ap.add_argument('--max-joint-slots',type=int,default=64,help='Joint-only safety cap; larger q remains unresolved')
    ap.add_argument('--joint-every',type=int,default=12)
    ap.add_argument('--no-free-probe',action='store_true')
    ap.add_argument('--allow-upstream-change',action='store_true')
    ap.add_argument('--output',type=Path,default=Path('batch_output'))
    ap.add_argument('--cache',type=Path,default=Path('.goma_batch_cache'))
    ap.add_argument('--solver-log',action='store_true')
    args=ap.parse_args(argv)
    if any(not math.isfinite(v) or v <= 0 for v in
           (args.seconds,args.oracle_seconds,args.master_seconds)):
        ap.error('Time budgets must be finite and positive')
    if any(not math.isfinite(v) or v < 0 for v in
           (args.relative_gap,args.absolute_gap)):
        ap.error('Gap tolerances must be finite and nonnegative')
    if min(args.threads,args.joint_slots,args.max_joint_slots,args.joint_every) < 0:
        ap.error('Thread, slot and iteration settings must be nonnegative')
    args.output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    progress_path=args.output/'progress.jsonl'
    try:
        data=json.loads(args.input.read_text(encoding='utf-8'))
        raw=data.get('cfg',data)
        keys=('L0','C1','C3','N_PE')+ENERGY_KEYS+('E_SRAM_leak','E_RF_leak')
        cfg={k:raw[k] for k in keys if k in raw}
        problem=Problem(cfg,data['B'],data.get('objective','edp'),data.get('leakage',False))
        up=Upstream(args.goma_root,allow_change=args.allow_upstream_change)
        params={'Threads':args.threads,'OutputFlag':int(args.solver_log)}
        seeds=up.seeds(problem)
        with progress_path.open('w',encoding='utf-8') as log:
            def progress(event):
                text=json.dumps(json_safe(event),ensure_ascii=False)
                log.write(text+'\n');log.flush()
                print(text,file=sys.stderr,flush=True)
            joint_disabled=False
            def joint(q,seconds,best):
                nonlocal joint_disabled
                if joint_disabled: return dict(status='JOINT_LICENSE_DISABLED',profiles=[],counts={})
                try:
                    return solve_joint_q(problem,up,q,seconds,params=params,incumbent=best,
                                         export_dir=args.output/'joint_models')
                except Exception as exc:
                    # A size-limited Gurobi license can still run the one-block oracle.
                    # Do not discard valid bounds, or silently substitute fake optima.
                    if getattr(exc,'errno',None)==10010:
                        joint_disabled=True
                        progress(dict(event='joint_unavailable',reason=str(exc)))
                        return dict(status='JOINT_LICENSE_LIMIT',profiles=[],counts={})
                    raise
            if args.method in ('hybrid','adaptive'):
                oracle=GurobiOracle(problem,up,params=params,cache_dir=args.cache,
                                     log_dir=args.output/'oracle_logs')
                try:
                    result=solve_adaptive(problem,oracle,seeds=seeds,seconds=max(0.001,args.seconds-(time.monotonic()-started)),
                        oracle_seconds=args.oracle_seconds,master_seconds=args.master_seconds,
                        backend=args.master_backend,rel_gap=args.relative_gap,abs_gap=args.absolute_gap,
                        free_probe=not args.no_free_probe,joint_callback=joint if args.method=='hybrid' else None,
                        joint_slots=args.joint_slots,joint_every=args.joint_every,on_progress=progress)
                    result['oracle_cache_hits']=oracle.cache_hits
                    result['oracle_model_optimizations']=oracle.calls
                    (args.output/'oracle_records.json').write_text(json.dumps(json_safe(oracle.history),indent=2))
                finally: oracle.close()
            else:
                budget=Budget(max(0.001,args.seconds-(time.monotonic()-started)))
                mr=solve_master(problem,[Option(p.id,p.n,p.c,p.e) for p in seeds],
                                seconds=min(args.master_seconds,budget.left()),backend=args.master_backend)
                best=schedule(problem,seeds,mr.counts) if mr.counts else None
                upper=best['normalized_objective'] if best else math.inf
                bounds={q:min(problem.score(q,s,q*problem.io_bound) for qq,s in problem.pairs() if qq==q)
                        for q in sorted(set(q for q,s in problem.pairs()))}
                records=[];unresolved=[]
                for q in sorted(bounds):
                    if bounds[q]>=upper: continue
                    if q>args.max_joint_slots or budget.left()<=0:
                        unresolved.append(q);continue
                    leftqs=sum(1 for qq in bounds if qq>=q and qq<=args.max_joint_slots)
                    rec=joint(q,budget.left()/max(1,leftqs),best)
                    if rec.get('lower') is not None: bounds[q]=max(bounds[q],rec['lower'])
                    if rec.get('schedule') and rec['schedule']['normalized_objective']<upper:
                        best=rec['schedule'];upper=best['normalized_objective']
                    if not closed_gap(upper,bounds[q],args.relative_gap,args.absolute_gap): unresolved.append(q)
                    records.append({k:v for k,v in rec.items() if k not in ('profiles','counts','schedule')})
                    progress(dict(event='joint_result',q=q,lower=bounds[q],upper=upper))
                unresolved=[q for q in bounds if not closed_gap(upper,bounds[q],args.relative_gap,args.absolute_gap)]
                lower=min(bounds.values(),default=math.inf)
                certified=closed_gap(upper,lower,args.relative_gap,args.absolute_gap)
                result=dict(status='GAP_CERTIFIED' if certified else ('INFEASIBLE' if math.isinf(lower) else 'TIME_OR_SCOPE_LIMIT'),
                            scope='all_periodic_partitions',schedule=best,normalized_lower_bound=lower,
                            normalized_upper_bound=upper,lower_bound=lower*problem.dimensional_scale,
                            upper_bound=upper*problem.dimensional_scale,relative_gap=gap(upper,lower),
                            q_bounds=bounds,unresolved_qs=unresolved,joint_records=records)
        result.update(method=args.method,problem=asdict(problem),upstream_commit=COMMIT,
                      upstream_blob_hashes=up.hashes,total_wall_seconds=time.monotonic()-started)
        dest=args.output/'result.json'
        dest.write_text(json.dumps(json_safe(result),ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(dest))
        return 0 if result['schedule'] or result['status']=='INFEASIBLE' else 2
    except Exception as exc:
        err=dict(status='ERROR',error_type=type(exc).__name__,message=str(exc),
                 elapsed=time.monotonic()-started)
        (args.output/'error.json').write_text(json.dumps(err,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        return 2
