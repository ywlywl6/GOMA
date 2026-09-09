import json
import math
from pathlib import Path
import random
import pytest
from goma_batch.core import Problem,Profile,Option,Cell,pareto,schedule,divisors
from goma_batch.master import solve_master
from goma_batch.adaptive import solve_adaptive
from goma_batch.upstream import Upstream,BLOBS,blob_sha
from .helpers import CatalogueOracle,exhaustive_value,convert_legacy
from .legacy_reference import enumerate_profiles

ROOT=Path(__file__).resolve().parents[1]
UP=Upstream(Path(__file__).parent/'upstream',evaluator_only=True)

def problem(name):
    d=json.loads((ROOT/'examples'/f'{name}.json').read_text())
    return Problem(d['cfg'],d['B'],d['objective'],d['leakage'])

def catalogue(p):
    raw,_=enumerate_profiles(tuple(p.cfg['L0'][d] for d in 'xyz'),p.cfg['C3'],p.C,p.N,
        tuple(int(p.cfg[k]) for k in ('E_DDR_r','E_DDR_w','E_SRAM_r','E_SRAM_w','E_RF_r','E_RF_w','E_MACC')))
    return [convert_legacy(UP,p,r) for r in raw]


def test_original_evaluator_hash():
    assert blob_sha((Path(__file__).parent/'upstream'/'normalized_energy_model.py').read_bytes())==BLOBS['normalized_energy_model.py']

@pytest.mark.parametrize('name',['scalar','threshold','nondivisible','leakage'])
@pytest.mark.parametrize('obj',['energy','edp'])
def test_catalogue_master_and_adaptive(name,obj):
    base=problem(name);p=Problem(base.cfg,base.B,obj,base.leakage)
    ps=catalogue(p)
    reference=exhaustive_value(p,ps)
    m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in ps],backend='scipy')
    assert math.isclose(m.objective,reference,rel_tol=1e-10,abs_tol=1e-9)
    assert math.isclose(m.lower,reference,rel_tol=1e-10,abs_tol=1e-9)
    oracle=CatalogueOracle(ps)
    result=solve_adaptive(p,oracle,seeds=UP.seeds(p),backend='scipy',seconds=30,
                          rel_gap=1e-9,abs_gap=1e-9)
    assert result['status']=='GAP_CERTIFIED'
    assert math.isclose(result['normalized_upper_bound'],reference,rel_tol=1e-9,abs_tol=1e-9)
    assert result['normalized_lower_bound']<=reference+1e-8
    out=result['schedule']
    assert sum(g['multiplicity']*g['n'] for g in out['allocation_groups'])==p.N
    assert sum(g['multiplicity']*g['C1_quota'] for g in out['allocation_groups'])==p.C
    for g in out['allocation_groups']:UP.evaluate(p,g['mapping'],n=g['n'],capacity=g['C1_quota'])


def test_scalar_counterexample():
    p=problem('scalar');ps=catalogue(p)
    m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in ps],backend='scipy')
    out=schedule(p,ps,m.counts)
    assert out['q']==2
    assert out['dynamic_energy']==6666
    assert out['total_cycles']==16
    assert out['edp']==106656


def test_nonuniform_memory_is_retained():
    p=problem('threshold');ps=catalogue(p)
    p2=[t for t in ps if t.n==2]
    m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in p2],backend='scipy')
    out=schedule(p,p2,m.counts)
    assert out['q']==2
    assert out['dynamic_energy']==2960
    assert out['total_cycles']==4
    assert sorted(g['footprint'] for g in out['allocation_groups'])==[0,4]


def test_invalid_q1_not_forced():
    p=problem('nondivisible');ps=catalogue(p)
    out=solve_adaptive(p,CatalogueOracle(ps),seeds=UP.seeds(p),backend='scipy',seconds=30)
    assert out['schedule']['q']==2
    assert out['schedule']['gamma']==1.5

@pytest.mark.parametrize('obj',['energy','edp'])
@pytest.mark.parametrize('leak',[False,True])
def test_random_masters_against_independent_dp(obj,leak):
    rng=random.Random(731)
    for _ in range(25):
        cfg=dict(L0=dict(x=4,y=2,z=2),C1=rng.randint(0,10),C3=1,N_PE=rng.randint(1,8),
                 E_DDR_r=1,E_DDR_w=1,E_SRAM_r=1,E_SRAM_w=1,E_RF_r=1,E_RF_w=1,E_MACC=1,
                 E_SRAM_leak=2.5,E_RF_leak=.4)
        p=Problem(cfg,rng.choice([1,2,3,4,6,8]),obj,leak)
        ps=[]
        for n in p.ns:
            for c in sorted(set([0,p.C//2,p.C])):
                ps.append(Profile(n,c,p.io_bound+rng.randint(0,100)/10,{'test_only':True}))
        expected=exhaustive_value(p,ps)
        m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in ps],backend='scipy')
        if math.isinf(expected):assert m.status=='INFEASIBLE'
        else:assert math.isclose(m.objective,expected,rel_tol=1e-10,abs_tol=1e-8)


def test_timeout_does_not_certify_plateau():
    p=problem('scalar');ps=catalogue(p)
    oracle=CatalogueOracle(ps,lower_shift=1.0,first_timeout=True)
    out=solve_adaptive(p,oracle,seeds=UP.seeds(p),backend='scipy',seconds=30,
                       free_probe=False,seed_q1=False,max_iterations=1,rel_gap=0,abs_gap=0)
    assert out['status']!='GAP_CERTIFIED'
    assert out['closed_cells']==0
    assert all(not event.get('proven_plateau',False) for event in out['history'])
    assert out['normalized_lower_bound']<=exhaustive_value(p,ps)


def test_strict_gap_reports_unresolved_numerics():
    p=problem('scalar');ps=catalogue(p)
    oracle=CatalogueOracle(ps,lower_shift=1e-5)
    out=solve_adaptive(p,oracle,seeds=UP.seeds(p),backend='scipy',seconds=30,
                       rel_gap=0,abs_gap=0)
    assert out['status']!='GAP_CERTIFIED'
    assert out['relative_gap']>0


def test_infeasible_pe_packing():
    cfg=dict(problem('threshold').cfg);cfg.update(L0=dict(x=3,y=1,z=1),N_PE=5)
    p=Problem(cfg,2)
    out=solve_adaptive(p,CatalogueOracle([]),seeds=UP.seeds(p),backend='scipy',
                       free_probe=False,seed_q1=False)
    assert out['status']=='INFEASIBLE'
    assert out['schedule'] is None


def test_pareto_never_crosses_pe_counts():
    ps=[Profile(1,0,3,{}),Profile(2,0,4,{}),Profile(2,1,5,{})]
    assert [(p.n,p.c,p.e) for p in pareto(ps)]==[(1,0,3),(2,0,4)]


def test_scaling_and_fixed_whole_device_leakage():
    p=problem('leakage');ps=catalogue(p)
    m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in ps],backend='scipy')
    out=schedule(p,ps,m.counts)
    assert out['leakage_energy']==p.power*out['total_cycles']
    assert math.isclose(out['edp'],out['normalized_objective']*p.dimensional_scale,rel_tol=1e-12)


def test_no_batch_slot_expansion_in_master():
    cfg=dict(problem('scalar').cfg);cfg['N_PE']=8
    p=Problem(cfg,10**9)
    ps=UP.seeds(p)
    m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in ps],backend='scipy')
    assert m.variables<200
    assert m.q in p.qs


def test_large_mapping_seeds_without_enumeration():
    p=problem('large_synthetic')
    ps=UP.seeds(p)
    assert len(ps)==len(p.ns)
    for t in ps:assert t.c==0 and t.e>=p.io_bound-1e-10
    m=solve_master(p,[Option(t.id,t.n,t.c,t.e) for t in ps],backend='scipy')
    assert m.counts and m.variables<2000


@pytest.mark.parametrize('field,value',[('B',0),('C1',-.5),('N_PE',True),('E_RF_r',float('nan'))])
def test_invalid_input(field,value):
    p=problem('scalar');cfg=dict(p.cfg);B=p.B
    if field=='B':B=value
    else:cfg[field]=value
    with pytest.raises(ValueError):Problem(cfg,B)


def test_random_adaptive_certificates():
    rng=random.Random(108)
    for _ in range(30):
        cfg=dict(problem('threshold').cfg);cfg.update(C1=rng.randint(0,12),N_PE=rng.randint(1,8))
        p=Problem(cfg,rng.choice([2,4,8]),rng.choice(['energy','edp']))
        ps=[]
        for n in p.ns:
            for c in sorted(set([0,p.C//3,2*p.C//3,p.C])):
                ps.append(Profile(n,c,p.io_bound+rng.randint(0,30),{'synthetic':True}))
        initial=pareto([t for t in ps if t.c==0])
        result=solve_adaptive(p,CatalogueOracle(ps,tie_largest=True),seeds=initial,
                              backend='scipy',seconds=10,rel_gap=1e-9,abs_gap=1e-9)
        expected=exhaustive_value(p,ps)
        if math.isinf(expected):assert result['status']=='INFEASIBLE'
        else:
            assert result['status']=='GAP_CERTIFIED'
            assert math.isclose(result['normalized_upper_bound'],expected,rel_tol=1e-9,abs_tol=1e-9)


def test_q_specific_joint_bounds_strengthen_not_exclude():
    p=problem('scalar');ps=catalogue(p)
    qs={q:exhaustive_value(p,ps,qs=[q]) for q in p.qs}
    loose=[Option(str(n),n,0,p.io_bound) for n in p.ns]
    got=solve_master(p,loose,backend='scipy',q_bounds=qs)
    assert math.isclose(got.lower,min(qs.values()),rel_tol=1e-12)


def test_zero_capacity_and_zero_energy():
    cfg=dict(problem('scalar').cfg);cfg['C1']=cfg['C3']=0
    for k in ('E_DDR_r','E_DDR_w','E_SRAM_r','E_SRAM_w','E_RF_r','E_RF_w','E_MACC'):cfg[k]=0
    p=Problem(cfg,2)
    ps=UP.seeds(p)
    result=solve_adaptive(p,CatalogueOracle(ps),seeds=ps,backend='scipy')
    assert result['status']=='GAP_CERTIFIED'
    assert result['normalized_upper_bound']==0
