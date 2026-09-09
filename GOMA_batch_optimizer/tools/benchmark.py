#!/usr/bin/env python3
"""Run the three real Gurobi-backed methods under matched budgets.

This harness is supplied for a licensed Gurobi environment. No saved result in
this package is presented as output from this harness. Each run uses a distinct,
cold oracle cache; Gurobi models are rebuilt in a fresh Python process.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

FIELDS = ('case','repeat','method','status','returncode','wall_seconds',
          'solver_wall_seconds','q','gamma','total_energy','total_cycles','edp',
          'normalized_lower_bound','normalized_upper_bound','relative_gap',
          'oracle_queries','oracle_model_optimizations','oracle_cache_hits','output')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('inputs', nargs='+', type=Path, help='JSON cases; all use their own objective')
    ap.add_argument('--goma-root', required=True, type=Path)
    ap.add_argument('--output', type=Path, default=Path('benchmark_output'))
    ap.add_argument('--seconds', type=float, default=300.0)
    ap.add_argument('--repeats', type=int, default=1)
    ap.add_argument('--threads', type=int, default=1)
    ap.add_argument('--grace', type=float, default=60.0, help='Process startup/model construction grace')
    ap.add_argument('--methods', nargs='+', choices=['adaptive','joint','hybrid'],
                    default=['adaptive','joint','hybrid'])
    args = ap.parse_args(argv)
    if args.seconds <= 0 or args.repeats < 1 or args.grace <= 0 or args.threads < 0:
        ap.error('Invalid budget, repeat count, or thread count')
    if len({p.stem for p in args.inputs}) != len(args.inputs):
        ap.error('Input filenames must have distinct stems')
    for p in args.inputs:
        if not p.is_file(): ap.error(f'Input not found: {p}')
    # Do not replace the required backend with an enumerator or a surrogate.
    try:
        import gurobipy as gp
    except ImportError:
        ap.error('gurobipy is required; install the solver extra and configure its license')
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    meta = dict(python=sys.version, platform=platform.platform(),
                gurobi_version=gp.gurobi.version(), args=vars(args),
                inputs={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in args.inputs}, cache_policy='distinct initially empty cache per run',
                budget_scope='CLI wall budget including construction; solver stops are not hard real-time')
    (args.output/'environment.json').write_text(json.dumps(meta, default=str, indent=2), encoding='utf-8')
    rows = []
    for repeat in range(args.repeats):
        # Rotate order to reduce systematic first/last-method effects.
        shift = repeat % len(args.methods)
        methods = args.methods[shift:] + args.methods[:shift]
        for case in args.inputs:
            for method in methods:
                dest = args.output / case.stem / f'repeat_{repeat+1}' / method
                if dest.exists() and any(dest.iterdir()):
                    raise FileExistsError(f'Refusing a nonempty run directory: {dest}')
                dest.mkdir(parents=True, exist_ok=True)
                cmd = [sys.executable,'-m','goma_batch',str(case.resolve()),
                       '--goma-root',str(args.goma_root.resolve()),'--method',method,
                       '--seconds',str(args.seconds),'--threads',str(args.threads),
                       '--master-backend','scipy','--output',str(dest.resolve()),
                       '--cache',str((dest/'cold_cache').resolve())]
                (dest/'command.json').write_text(json.dumps(cmd, indent=2), encoding='utf-8')
                env = dict(os.environ)
                env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH','')
                started = time.monotonic()
                timedout = False
                with (dest/'stdout.txt').open('w',encoding='utf-8') as out, \
                     (dest/'stderr.txt').open('w',encoding='utf-8') as err:
                    try:
                        cp = subprocess.run(cmd, stdout=out, stderr=err, env=env,
                                            timeout=args.seconds+args.grace, check=False)
                        rc = cp.returncode
                    except subprocess.TimeoutExpired:
                        timedout = True
                        rc = -1
                result_path = dest/'result.json'
                result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
                sched = result.get('schedule') or {}
                row = dict(case=case.stem,repeat=repeat+1,method=method,
                           status='HARNESS_TIMEOUT' if timedout else result.get('status','ERROR'),
                           returncode=rc,wall_seconds=time.monotonic()-started,
                           solver_wall_seconds=result.get('total_wall_seconds'),output=str(dest))
                for k in ('q','gamma','total_energy','total_cycles','edp'): row[k]=sched.get(k)
                for k in ('normalized_lower_bound','normalized_upper_bound','relative_gap',
                          'oracle_queries','oracle_model_optimizations','oracle_cache_hits'):
                    row[k]=result.get(k)
                rows.append(row)
                with (args.output/'summary.csv').open('w',newline='',encoding='utf-8') as handle:
                    writer=csv.DictWriter(handle,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
                print(json.dumps(row,ensure_ascii=False),flush=True)
    return int(any(r['returncode'] != 0 for r in rows))


if __name__ == '__main__':
    raise SystemExit(main())
