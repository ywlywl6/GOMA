#!/usr/bin/env python3
"""Solve the exported example without importing the root-level GOMA code."""
import argparse
import json
from pathlib import Path

import gurobipy as gp


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=here / "rerun")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output == here:
        parser.error("Use a separate output directory to preserve the supplied artifacts.")
    output.mkdir(parents=True, exist_ok=True)
    reference = json.loads((here / "practical_case_current_solver_record.json").read_text())
    log = output / "solver.log"
    log.write_text("")
    with gp.Env(params={"OutputFlag": 0}) as env:
        with gp.read(str(here / "practical_case_current_model.lp"), env=env) as model:
            for name, value in reference["parameters"].items():
                model.setParam(name, value)
            model.Params.LogFile = str(log)
            model.optimize()
            names = ["Status", "SolCount", "Runtime", "NodeCount", "ObjBound", "ObjBoundC"]
            if model.SolCount:
                names += ["ObjVal", "MIPGap", "MaxVio", "ConstrVio", "BoundVio", "IntVio"]
                model.write(str(output / "solution.sol"))
            else:
                (output / "solution.sol").unlink(missing_ok=True)
            attributes = {name: model.getAttr(name) for name in names}
            record = {
                "model_variant": "review1_bugfix",
                "model_file": "practical_case_current_model.lp",
                "gurobi_version": list(gp.gurobi.version()),
                "cfg": reference["cfg"],
                "parameters": {name: getattr(model.Params, name) for name in reference["parameters"]},
                "attributes": attributes,
            }
            if model.SolCount:
                record["public_variables"] = {
                    name: model.getVarByName(name).X for name in reference["public_variables"]
                }
            (output / "solver_record.json").write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps(attributes, indent=2))
            print(f"Outputs: {output}")


if __name__ == "__main__":
    main()
