"""Test-only catalogue oracle and independent exhaustive allocation DP."""
import math
from goma_batch.core import Profile, OracleResult

class CatalogueOracle:
    def __init__(self, profiles, *, lower_shift=0.0, tie_largest=False, first_timeout=False):
        self.profiles=profiles; self.calls=0; self.requests=[]
        self.lower_shift=lower_shift;self.tie_largest=tie_largest;self.first_timeout=first_timeout
    def solve(self,n,capacity,seconds):
        self.calls+=1;self.requests.append((n,capacity))
        candidates=[p for p in self.profiles if p.c<=capacity and (n is None or p.n==n)]
        p=min(candidates,key=lambda p:(p.e,-p.c if self.tie_largest else p.c))
        timeout=self.first_timeout and self.calls==1
        return OracleResult(n,capacity,max(0,p.e-self.lower_shift),p,not timeout,
                            'TEST_TIME_LIMIT' if timeout else 'TEST_EXACT',raw_objective=p.e,raw_bound=p.e-self.lower_shift)


def exhaustive_value(problem,profiles,qs=None):
    """Unpruned DP over exact resources; does not reuse the count MILP."""
    qs=problem.qs if qs is None else qs
    if not qs:return math.inf
    states={(0,0,problem.N+1):0.0};best=math.inf
    for k in range(1,max(qs)+1):
        nxt={}
        for (n,c,s),value in states.items():
            for p in profiles:
                if n+p.n>problem.N or c+p.c>problem.C:continue
                key=(n+p.n,c+p.c,min(s,p.n))
                nxt[key]=min(nxt.get(key,math.inf),value+p.e)
        states=nxt
        if k in qs:
            for (n,c,s),value in states.items():
                if n==problem.N:best=min(best,problem.score(k,s,value))
    return best


def convert_legacy(up,problem,record):
    d='xyz';m=record['mapping']
    mapping=dict(hatL_12={d[i]:m['l1'][i]//m['l2'][i] for i in range(3)},
        hatL_23={d[i]:m['l2'][i]//m['l3'][i] for i in range(3)},
        hatL_34=dict(zip(d,m['l3'])),B1=dict(zip(d,m['b1'])),B3=dict(zip(d,m['b3'])),
        alpha01=d[m['a0']],alpha12=d[m['a1']])
    p=up.evaluate(problem,mapping,n=record['n'])
    assert math.isclose(p.e*problem.V,record['e'],rel_tol=1e-12,abs_tol=1e-9)
    return p
