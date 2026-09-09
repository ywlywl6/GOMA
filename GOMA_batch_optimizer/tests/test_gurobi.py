"""Real backend integration tests. Skips are explicit, never counted as passes."""
import math
import pytest
from goma_batch.core import Problem
from goma_batch.upstream import GurobiOracle,PARAMS
from goma_batch.joint import build_joint,solve_joint_q
from .test_batch import problem,catalogue
from .helpers import exhaustive_value

@pytest.mark.gurobi
@pytest.mark.parametrize('objective',['energy','edp'])
def test_joint_q1_equals_unmodified_goma(solver_upstream,objective):
    up=solver_upstream;base=problem('scalar');p=Problem(base.cfg,base.B,objective)
    original=up.full.build_model_full(p.cfg,PARAMS)[0]
    try:
        original.Params.TimeLimit=30;original.optimize()
        assert original.SolCount and original.Status==2
        expected=float(original.ObjVal)
        got=solve_joint_q(p,up,1,30)
        assert got['schedule']
        assert math.isclose(got['schedule']['normalized_objective'],expected,rel_tol=1e-7)
    finally:original.dispose()

@pytest.mark.gurobi
def test_variable_pe_oracle_and_cache(solver_upstream,tmp_path):
    p=problem('scalar');o=GurobiOracle(p,solver_upstream,cache_dir=tmp_path)
    try:
        a=o.solve(1,0,30);b=o.solve(2,0,30);f=o.solve(None,p.C,30)
        assert a.optimal and b.optimal and f.optimal
        assert math.isclose(a.profile.e*p.V,3333,abs_tol=1e-6)
        assert math.isclose(b.profile.e*p.V,3334,abs_tol=1e-6)
        assert math.isclose(f.lower*p.V,3333,abs_tol=1e-6)
        assert o.solve(1,0,30).optimal and o.cache_hits==1
    finally:o.close()

@pytest.mark.gurobi
@pytest.mark.parametrize('name',['threshold','nondivisible','leakage'])
def test_joint_q2_equals_exhaustive_catalogue(solver_upstream,name):
    p=problem(name);ps=catalogue(p)
    try:result=solve_joint_q(p,solver_upstream,2,60)
    except Exception as e:
        if getattr(e,'errno',None)==10010:pytest.skip('Joint q=2 requires a larger Gurobi license')
        raise
    expected=exhaustive_value(p,ps,qs=[2])
    assert result['schedule']
    assert math.isclose(result['schedule']['normalized_objective'],expected,rel_tol=1e-7)
    assert result['lower']<=expected+1e-6
