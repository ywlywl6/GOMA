#!/usr/bin/env python3
"""Run source-evaluator fidelity and test-oracle allocation checks (NO Gurobi)."""
import csv
import json
import math
from pathlib import Path
import platform
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import scipy
import numpy
from goma_batch.core import Problem,Option
from goma_batch.upstream import Upstream,BLOBS
from goma_batch.adaptive import solve_adaptive
from goma_batch.master import solve_master
from tests.legacy_reference import enumerate_profiles
from tests.helpers import convert_legacy,CatalogueOracle,exhaustive_value

start=time.monotonic();dest=ROOT/'results';dest.mkdir(exist_ok=True)
up=Upstream(ROOT/'tests/upstream',evaluator_only=True)
ert=dict(E_DDR_r=100,E_DDR_w=100,E_SRAM_r=10,E_SRAM_w=10,E_RF_r=1,E_RF_w=1,E_MACC=1)
rows=[]
for shape,C,N in [((1,16,1),2,16),((2,2,2),16,8),((4,4,4),32,8),((2,4,4),32,8)]:
    p=Problem(dict(L0=dict(zip('xyz',shape)),C1=C,C3=1,N_PE=N,**ert),4)
    observed=[0];maxerr=[0.0]
    def observe(rec):
        prof=convert_legacy(up,p,rec)
        maxerr[0]=max(maxerr[0],abs(prof.e*p.V-rec['e']))
        observed[0]+=1
    raw,count=enumerate_profiles(shape,1,C,N,observer=observe)
    assert count==observed[0]
    rows.append(dict(shape=shape,C1=C,C3=1,N_PE_max=N,checked_mappings=count,
                     frontier_profiles=len(raw),maximum_total_energy_error=maxerr[0]))
assert sum(r['checked_mappings'] for r in rows)==48444
allocation=[]
for name in ['scalar','threshold','nondivisible','leakage']:
    inp=json.loads((ROOT/'examples'/f'{name}.json').read_text())
    for objective in ['energy','edp']:
        p=Problem(inp['cfg'],inp['B'],objective,inp['leakage'])
        raw,_=enumerate_profiles(tuple(p.cfg['L0'][d] for d in 'xyz'),p.cfg['C3'],p.C,p.N)
        ps=[convert_legacy(up,p,r) for r in raw]
        expected=exhaustive_value(p,ps)
        oracle=CatalogueOracle(ps)
        result=solve_adaptive(p,oracle,seeds=up.seeds(p),backend='scipy',rel_gap=1e-10,abs_gap=1e-10)
        assert result['status']=='GAP_CERTIFIED'
        assert math.isclose(result['normalized_upper_bound'],expected,rel_tol=1e-10)
        for h in result['history']:
            assert h['lower']<=expected+1e-8
        sc=result['schedule']
        allocation.append(dict(case=name,objective=objective,q=sc['q'],total_energy=sc['total_energy'],
                               total_cycles=sc['total_cycles'],edp=sc['edp'],gamma=sc['gamma'],
                               oracle_requests=oracle.calls,complete_catalogue_size=len(ps),
                               gap=result['relative_gap'],oracle_backend='test-only exact catalogue'))
        result['oracle_backend']='test-only exact catalogue'
        (dest/f'verified_{name}_{objective}.json').write_text(json.dumps(result,indent=2))
with (dest/'allocation_verification.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=allocation[0].keys());w.writeheader();w.writerows(allocation)
large=json.loads((ROOT/'examples/large_synthetic.json').read_text())
p=Problem(large['cfg'],large['B'])
ps=up.seeds(p)
m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in ps],backend='scipy')
report=dict(source_commit='b4015e465d78a8dbeb25ec9220cfe7c34883a865',source_blob_hashes=up.hashes,
            suites=rows,total_checked_mappings=sum(r['checked_mappings'] for r in rows),
            retained_frontier_profiles=sum(r['frontier_profiles'] for r in rows),
            allocation_cases=allocation,
            large_master_only=dict(shape=p.cfg['L0'],B=p.B,N=p.N,C1=p.C,
                input_profiles=len(ps),master_variables=m.variables,master_runtime=m.runtime,
                note='Known all-bypass profiles only. This is NOT a full Gurobi batch benchmark.'),
            environment=dict(python=sys.version,scipy=scipy.__version__,numpy=numpy.__version__,
                             platform=platform.platform(),gurobi_available=__import__('importlib.util',fromlist=['find_spec']).find_spec('gurobipy') is not None),
            runtime=time.monotonic()-start,
            not_executed=['Gurobi oracle optimization','Gurobi direct joint MIQCP optimization',
                          'Timeloop/Accelergy','end-to-end large-workload performance comparison'])
(dest/'verification_report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
