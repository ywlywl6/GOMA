#!/usr/bin/env python3
"""Standalone, small-instance verification for the GOMA batch extension.

This is a reference enumerator of the supplied analytical model, NOT the
original GOMA implementation or a Timeloop evaluation. It uses the Python
standard library only. Large workloads should use the existing GOMA solver
to populate the profile interface rather than exhaustive enumeration.

Run: python GOMA_batch_reference.py --output-dir batch_verification
Profile interface: {"n": active PEs, "c": minimum SRAM words,
                    "e": per-GEMM dynamic energy including MACs,
                    "mapping": optional mapping witness}.
"""
from itertools import product
from functools import lru_cache
import argparse
import csv
import json
from pathlib import Path
from fractions import Fraction
from math import isqrt

@lru_cache(None)
def divs(n):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("n must be a positive integer")
    small = [i for i in range(1, isqrt(n)+1) if n % i == 0]
    return tuple(sorted(set(small + [n//i for i in small])))
@lru_cache(None)
def facts4(n):
    return tuple((a,b,c,n//(a*b*c)) for a in divs(n) for b in divs(n//a) for c in divs(n//a//b))

MASKS=tuple(product((0,1),repeat=3))

def enumerate_profiles(shape, C3, Cmax, Nmax, ert=(100,100,10,10,1,1,1), observer=None):
    """Exact integer access accounting from the supplied GOMA model."""
    if len(shape) != 3 or any(not isinstance(x, int) or x < 1 for x in shape):
        raise ValueError("shape must contain three positive integers")
    if min(C3, Cmax) < 0 or Nmax < 1:
        raise ValueError("invalid resource limits")
    if len(ert) != 7 or any(not isinstance(x, int) or x < 0 for x in ert):
        raise ValueError("reference enumeration requires seven nonnegative integer ERT entries")
    R0,W0,R1,W1,R3,W3,mac=ert
    V=shape[0]*shape[1]*shape[2]
    best={}; count=0
    for axis_factors in product(*(facts4(x) for x in shape)):
        k0,k1,s,l3=zip(*axis_factors)
        n=s[0]*s[1]*s[2]
        if n>Nmax: continue
        l2=tuple(s[d]*l3[d] for d in range(3))
        l1=tuple(k1[d]*l2[d] for d in range(3))
        a0s=[d for d in range(3) if k0[d]>1] or [0]
        a1s=[d for d in range(3) if k1[d]>1]
        footprints1=tuple(l1[(d+1)%3]*l1[(d+2)%3] for d in range(3))
        footprints3=tuple(l3[(d+1)%3]*l3[(d+2)%3] for d in range(3))
        masks1=[(b,sum(b[d]*footprints1[d] for d in range(3))) for b in MASKS]
        masks1=[x for x in masks1 if x[1]<=Cmax]
        masks3=[b for b in MASKS if sum(b[d]*footprints3[d] for d in range(3))<=C3]
        for a0 in a0s:
            for a1 in a1s or [a0]:
                chi=tuple(d==a0==a1 and all(k1[u]==1 for u in range(3) if u!=d) for d in range(3))
                n01=tuple(V//(shape[d] if d==a0 else l1[d]) for d in range(3))
                nr=tuple(V//(l3[d]*(k1[d] if d==a1 else 1)*(k0[d] if chi[d] else 1)) for d in range(3))
                ns=tuple(nr[d]//s[d] for d in range(3))
                assert all(nr[d]%s[d]==0 for d in range(3))
                F=shape[0]*shape[1]
                for b1,c in masks1:
                    for b3 in masks3:
                        e=V*mac
                        for d in (0,1):
                            if b1[d]:e+=n01[d]*(R0+W1)
                            if b3[d]:e+=nr[d]*W3+ns[d]*(R1 if b1[d] else R0)
                            e+=(V*R3 if b3[d] else (V//s[d])*(R1 if b1[d] else R0))
                        if b1[2]:e+=n01[2]*W0+(n01[2]-F)*(R0+W1)
                        if b3[2]:
                            e+=(nr[2]-F*s[2])*W3
                            e+=ns[2]*(W1 if b1[2] else W0)+(ns[2]-F)*(R1 if b1[2] else R0)
                            e+=V*W3+(V-F*s[2])*R3
                        else:
                            e+=(V//s[2])*(W1 if b1[2] else W0)+(V//s[2]-F)*(R1 if b1[2] else R0)
                        count+=1
                        if observer is not None:
                            observer(dict(n=n,c=c,e=e,mapping=dict(l1=l1,l2=l2,l3=l3,a0=a0,a1=a1,b1=b1,b3=b3,n=n,c=c)))
                        key=n,c
                        if key not in best or e<best[key][0]:
                            best[key]=(e,dict(l1=l1,l2=l2,l3=l3,a0=a0,a1=a1,b1=b1,b3=b3,n=n,c=c))
    profiles=[]
    for n in sorted(set(k[0] for k in best)):
        val=float('inf')
        for nn,c in sorted(k for k in best if k[0]==n):
            e,m=best[nn,c]
            if e<val:
                profiles.append(dict(n=n,c=c,e=e,mapping=m));val=e
    return profiles, count

def table(profiles,Nmax,Cmax):
    f={}
    for n in range(1,Nmax+1):
        for c in range(Cmax+1):
            f[n,c]=min((p['e'] for p in profiles if p['n']==n and p['c']<=c),default=float('inf'))
    return f


def literal_energy(shape,m,ert=(100,100,10,10,1,1,1)):
    R0,W0,R1,W1,R3,W3,mac=ert
    V=shape[0]*shape[1]*shape[2]; l1=m['l1'];l2=m['l2'];l3=m['l3']
    k0=tuple(shape[d]//l1[d] for d in range(3)); k1=tuple(l1[d]//l2[d] for d in range(3));s=tuple(l2[d]//l3[d] for d in range(3))
    b1=m['b1'];b3=m['b3'];a0=m['a0'];a1=m['a1']
    chi=tuple(d==a0==a1 and all(k1[u]==1 for u in range(3) if u!=d) for d in range(3))
    h1=[Fraction(V,shape[d] if d==a0 else l1[d]) for d in range(3)]
    h3=[Fraction(V,l3[d]*(k1[d] if a1==d else 1)*(k0[d] if chi[d] else 1)) for d in range(3)]
    Fs={1:shape[0]*shape[1],3:shape[0]*shape[1]*s[2],4:shape[0]*shape[1]*s[2]}
    rho={1:1-Fraction(Fs[1],h1[2]),3:1-Fraction(Fs[3],h3[2]),4:1-Fraction(Fs[4],V)}
    assert all(0<=r<=1 for r in rho.values())
    e=Fraction(V*mac)
    for d in range(3):
        for p in (1,3,4):
            rz=rho[p]
            if p==1:
                nr=ns=b1[d]*h1[d]
                source=(R0 if d!=2 else W0+rz*R0)
                recv=(W1 if d!=2 else rz*W1)
            elif p==3:
                nr=b3[d]*h3[d];ns=nr/s[d]
                R,W=(R1,W1) if b1[d] else (R0,W0)
                source=(R if d!=2 else W+rz*R)
                recv=(W3 if d!=2 else rz*W3)
            else:
                nr=V;ns=Fraction(V) if b3[d] else Fraction(V,s[d])
                R,W=(R3,W3) if b3[d] else ((R1,W1) if b1[d] else (R0,W0))
                source=(R if d!=2 else W+rz*R)
                recv=0
            e+=ns*source+nr*recv
    return e



def solve_outer(profiles, *, B, C1, N, V, objective='edp', q_only=None, leak_per_cycle=0):
    """Exact sparse DP on a supplied COMPLETE profile catalogue.

    Retains the actual minimum PE count, so unequal allocations are allowed.
    The result is globally optimal only over the input catalogue. Unused SRAM
    quota is assigned to one slot after optimization; no PE is left unassigned.
    Constant whole-device leakage is charged for the full makespan.
    """
    if min(B, N, V) < 1 or C1 < 0 or objective not in ('energy', 'edp'):
        raise ValueError('invalid outer problem')
    if q_only is not None and (q_only < 1 or B % q_only):
        raise ValueError('q_only must be a positive divisor of B')
    leak = Fraction(str(leak_per_cycle))
    if leak < 0: raise ValueError('leakage must be nonnegative')
    ps=[]
    for p in profiles:
        n,c=int(p['n']),int(p['c'])
        if n < 1 or c < 0 or V % n: raise ValueError('invalid profile resource requirement')
        energy = Fraction(str(p['e']))
        if energy < 0: raise ValueError('negative profile energy')
        if n <= N and c <= C1: ps.append({**p,'n':n,'c':c,'energy_exact':energy})
    candidates=[q_only] if q_only else [q for q in divs(B) if q <= N]
    if not candidates: raise ValueError('no candidate q')
    # State = (used PEs, used SRAM footprint, smallest PE count).
    # Value = (energy of one round, tuple of chosen profile indices).
    states={(0,0,N+1):(Fraction(0),())}
    best=None; state_counts=[]
    for k in range(1,max(candidates)+1):
        new={}
        for (p,c,m),(energy,chosen) in states.items():
            for j,profile in enumerate(ps):
                pp,cc=p+profile['n'],c+profile['c']
                if pp>N or cc>C1: continue
                key=(pp,cc,min(m,profile['n']))
                value=(energy+profile['energy_exact'],chosen+(j,))
                if key not in new or value[0]<new[key][0]:new[key]=value
        # Safe dominance: same k, used PEs and min-PE; no more memory and no more energy.
        groups={}
        for key,value in new.items():groups.setdefault((key[0],key[2]),[]).append((key,value))
        states={}
        for entries in groups.values():
            current=None
            for key,value in sorted(entries,key=lambda kv:kv[0][1]):
                if current is None or value[0]<current:
                    states[key]=value;current=value[0]
        state_counts.append(len(states))
        if k not in candidates:continue
        for (p,c,m),(S,chosen) in states.items():
            if p!=N:continue
            T=(B//k)*(V//m)
            dynamic=(B//k)*S
            energy=dynamic+leak*T
            edp=energy*T
            key=(energy if objective=='energy' else edp,T,k)
            if best is None or key<best['_key']:
                slots=[dict(ps[j]) for j in chosen]
                for slot in slots:slot.pop('energy_exact',None)
                allocations=[slot['c'] for slot in slots]
                allocations[0]+=C1-c
                best=dict(_key=key,q=k,pe=[p['n'] for p in slots],sram=allocations,
                          footprint=[p['c'] for p in slots],dynamic_energy=dynamic,
                          energy=energy,cycles=T,edp=edp,profiles=slots,
                          gamma=Fraction(N,k*m))
    if best is None:raise ValueError('infeasible outer problem for the supplied catalogue')
    best.pop('_key');best['state_counts']=state_counts
    return best


def jsonable(obj):
    if isinstance(obj,Fraction):return obj.numerator if obj.denominator==1 else str(obj)
    if isinstance(obj,dict):return {k:jsonable(v) for k,v in obj.items()}
    if isinstance(obj,(tuple,list)):return [jsonable(v) for v in obj]
    return obj


def run_verification(output_dir):
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    configurations=[((1,16,1),1,2,16),((2,2,2),1,16,8),
                    ((4,4,4),1,32,8),((2,4,4),1,32,8)]
    cache={};stats=[]
    for shape,C3,Cmax,Nmax in configurations:
        profiles,count=enumerate_profiles(shape,C3,Cmax,Nmax)
        for p in profiles:
            assert literal_energy(shape,p['mapping'])==p['e']
            assert p['n']==p['mapping']['n']
        cache[shape]=profiles
        stats.append(dict(X=shape[0],Y=shape[1],Z=shape[2],C3=C3,Cmax=Cmax,
                          Nmax=Nmax,legal_parameter_configurations=count,
                          nondominated_profiles=len(profiles)))
        name='profiles_'+'_'.join(map(str,shape))+'.json'
        (output/name).write_text(json.dumps(profiles,indent=2),encoding='utf-8')
    scalar=cache[(1,16,1)]
    f=table(scalar,16,2)
    assert [f[n,2] for n in (1,2,4,8,16)]==[3333,3334,3336,3340,3316]
    serial=solve_outer(scalar,B=2,C1=2,N=2,V=16,q_only=1)
    batch=solve_outer(scalar,B=2,C1=2,N=2,V=16)
    assert (serial['energy'],serial['cycles'],serial['edp'])==(6668,16,106688)
    assert (batch['q'],batch['energy'],batch['cycles'],batch['edp'])==(2,6666,16,106656)
    cube=cache[(2,2,2)];fc=table(cube,8,16)
    assert [fc[2,c] for c in range(7)]==[1620,1620,1620,1620,1340,1340,1340]
    fixedq=solve_outer(cube,B=2,C1=6,N=4,V=8,q_only=2)
    assert fixedq['energy']==2960 and fixedq['cycles']==4
    assert 2*fc[2,3]==3240
    # Tight lower bound when all compute points can be spatially unfolded.
    tight=solve_outer(scalar,B=2,C1=2,N=16,V=16)
    assert tight['q']==1 and tight['energy']==6632 and tight['cycles']==2
    # All-powered leakage changes neither the equal-latency comparison nor the inner map.
    leaked=solve_outer(scalar,B=2,C1=2,N=2,V=16,leak_per_cycle=10)
    assert leaked['q']==2 and leaked['energy']==6826
    # Divisibility constraints can rule out q=1 while permitting a batch split.
    feasible=solve_outer(cube,B=2,C1=6,N=6,V=8)
    assert feasible['q']==2 and sorted(feasible['pe'])==[2,4]
    records=[]
    for name,r in [('scalar_serial',serial),('scalar_batch',batch),
                   ('cube_fixed_q_optimum',fixedq),('scalar_full_spatial',tight),
                   ('scalar_leakage_10',leaked),('cube_q1_infeasible',feasible)]:
        records.append(dict(case=name,q=r['q'],PEs=str(r['pe']),SRAM=str(r['sram']),
                            dynamic_energy=jsonable(r['dynamic_energy']),
                            total_energy=jsonable(r['energy']),cycles=r['cycles'],
                            EDP=jsonable(r['edp']),gamma=jsonable(r['gamma'])))
    with (output/'verification_summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=records[0].keys());w.writeheader();w.writerows(records)
    with (output/'enumeration_counts.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=stats[0].keys());w.writeheader();w.writerows(stats)
    detail=dict(ert=dict(R0=100,W0=100,R1=10,W1=10,R3=1,W3=1,MAC=1),
                serial=serial,batch=batch,fixed_q_asymmetry=fixedq,
                full_spatial=tight,leakage=leaked,q1_infeasible=feasible,
                enumeration_counts=stats,checks='all assertions passed')
    (output/'verification_details.json').write_text(json.dumps(jsonable(detail),indent=2),encoding='utf-8')
    print('All assertions passed. Wrote',output.resolve())
    for row in records:print(row)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',default='batch_verification')
    args=parser.parse_args()
    run_verification(args.output_dir)
