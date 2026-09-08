"""Regression checks for reviewed traffic, solver extraction and mesh conversion."""
from dataclasses import asdict
from itertools import product
from pathlib import Path
import json
import math
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from full_model import build_model_full
from main import make_cfg
from normalized_energy_model import DeviceParams, compute_normalized_total_energy
from solver import _solve_full_model, _extract_mapping_params, integer_value
from gen_problem_mapping import copy_template, update_mapping_file
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ReviewedModelTests(unittest.TestCase):
    def test_fixed_geometry_matches_independent_formula(self):
        # Reduction traffic, cross-tile RF reuse, degenerate walking axes,
        # and all four source/receiver residency choices.
        params = DeviceParams(64, 63, 7, 6, .7, 1.1, 1.4)
        for degenerate, keep1, keep3 in product((False, True), repeat=3):
            with self.subTest(degenerate=degenerate, B1=keep1, B3=keep3):
                hats = [dict(x=4,y=4,z=8),
                        dict(x=1,y=1,z=1 if degenerate else 4),
                        dict(x=1,y=2,z=2), dict(x=2,y=1,z=2)]
                L0 = {d: math.prod(h[d] for h in hats) for d in 'xyz'}
                cfg = make_cfg(L0, asdict(params), 1000000, 10000, 4)
                m, L, k, y, B1, B3, a01, a12 = build_model_full(cfg, {
                    'OutputFlag': 0, 'NonConvex': 2, 'IntFeasTol': 1e-9,
                    'FeasibilityTol': 1e-9, 'MIPGap': 0, 'NumericFocus': 3,
                    'DualReductions': 0})
                try:
                    for (level, d), var in k.items():
                        var.LB = var.UB = hats[level][d]
                    for d in 'xyz':
                        B1[d].LB = B1[d].UB = int(keep1)
                        B3[d].LB = B3[d].UB = int(keep3)
                        a01[d].LB = a01[d].UB = int(d == 'z')
                        a12[d].LB = a12[d].UB = int(d == 'z')
                    m.optimize()
                    self.assertEqual(m.Status, 2)
                    energy, _ = compute_normalized_total_energy(
                        L0=L0, hatL_12=hats[1], hatL_23=hats[2], hatL_34=hats[3],
                        alpha01='z', alpha12='z',
                        B={1: dict.fromkeys('xyz',int(keep1)),3: dict.fromkeys('xyz',int(keep3))},
                        params=params, include_leak=False)
                    self.assertAlmostEqual(m.ObjVal, energy, delta=1e-5+1e-6*abs(energy))
                finally:
                    m.dispose()

    def test_published_certificate_mapping(self):
        record = json.loads((ROOT/'solver_evidence/practical_case_current_solver_record.json').read_text())
        cfg = record['cfg']
        variables = record['public_variables']
        m, *_ = build_model_full(cfg, {'OutputFlag':0, 'IntFeasTol':1e-9,
                                      'FeasibilityTol':1e-9, 'NumericFocus':3, 'DualReductions':0})
        try:
            m.update()
            for name,value in variables.items():
                var=m.getVarByName(name)
                var.LB=var.UB=value
            m.optimize()
            self.assertEqual(m.Status,2)
            self.assertAlmostEqual(m.ObjVal, record['attributes']['ObjVal'], delta=1e-7)
        finally:
            m.dispose()

    def test_integer_extraction_rounds_and_rejects_invalid_values(self):
        self.assertEqual(integer_value(15.999999999),16)
        self.assertEqual(integer_value(.999999999),1)
        for invalid in (1.01,float('nan'),float('inf')):
            with self.assertRaises(RuntimeError):
                integer_value(invalid)
        cfg={'L0':dict.fromkeys('xyz',16)}
        k={(p,d):SimpleNamespace(X=1.999999999) for p in range(4) for d in 'xyz'}
        bits={d:SimpleNamespace(X=.999999999) for d in 'xyz'}
        axis={d:SimpleNamespace(X=float(d=='x')) for d in 'xyz'}
        result=_extract_mapping_params(cfg,{},k,bits,bits,axis,axis)
        self.assertEqual(result[0],dict.fromkeys('xyz',2))
        k[(0,'x')].X=3
        with self.assertRaises(RuntimeError):
            _extract_mapping_params(cfg,{},k,bits,bits,axis,axis)

    def test_no_incumbent_does_not_read_solution(self):
        # TIME_LIMIT is not evidence of a feasible incumbent.
        fake=SimpleNamespace(optimize=lambda:None,Status=9,SolCount=0)
        with self.assertRaisesRegex(RuntimeError,'No feasible incumbent'):
            _solve_full_model({},build_model_full=lambda *a,**kw:(fake,)*8,verbose=False)

    def test_2d_mesh_preserves_spatial_factors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'mapping.yaml'
            copy_template(ROOT/'templates/mapping_template.yaml',path)
            h=dict(x=4,y=8,z=8)
            unit=dict.fromkeys('xyz',1)
            update_mapping_file(path,unit,unit,h,unit,'x','x',unit,unit,mesh_x=16,mesh_y=16)
            entries=[e for e in yaml.safe_load(path.read_text())['mapping'] if e['type']=='spatial']
            self.assertEqual(len(entries),2)
            vectors=[{v.split('=')[0].lower():int(v.split('=')[1]) for v in e['factors']} for e in entries]
            self.assertEqual([math.prod(v.values()) for v in vectors],[16,16])
            self.assertEqual({d:math.prod(v[d] for v in vectors) for d in 'xyz'},h)
            update_mapping_file(path,unit,unit,h,unit,'x','x',unit,unit,mesh_x=256,mesh_y=1)
            entries=[e for e in yaml.safe_load(path.read_text())['mapping'] if e['type']=='spatial']
            self.assertEqual(len(entries),1)
            self.assertNotIn('split',entries[0])


if __name__ == '__main__':
    unittest.main()
