import os
import pytimeloop.timeloopfe.v4 as tl
from pytimeloop.timeloopfe.v4.arch import Storage, Container


def main():
    here = os.path.abspath(os.path.dirname(__file__))

    # Top-level Jinja spec (architecture/problem/etc.)
    top = os.path.join(here, "top_model.jinja")

    # ERT produced by running the model (unit energy per component/action)
    ert_yaml = os.path.join(here, "outputs_my", "timeloop-model.ERT.yaml")

    if not os.path.exists(ert_yaml):
        print("ERT file not found at:", ert_yaml)
        print("Please run run_model.py first to generate it.")
        return

    # Build v4 Specification; ERT.yaml contributes the top-level ERT key
    spec = tl.Specification.from_yaml_files(top, ert_yaml)

    # If there are expressions depending on variables, this resolves them
    spec.parse_expressions()

    # ------------------------------------------------------------------
    # 1) Architecture: depth/width of storage levels, and PE meshX/meshY
    # ------------------------------------------------------------------
    print("=== Architecture storage depth/width ===")
    for buf in spec.architecture.get_nodes_of_type(Storage):
        name = buf.name
        depth = buf.attributes.depth
        width = buf.attributes.width
        print(f"{name}: depth={depth}, width={width}")

    print("\n=== PE meshX / meshY ===")
    pe_found = False
    pe_mesh_x = pe_mesh_y = None
    pey_mesh_x = pey_mesh_y = None
    for cont in spec.architecture.get_nodes_of_type(Container):
        if cont.name == "PE":
            pe_mesh_x = int(getattr(cont.spatial, "meshX", 1))
            pe_mesh_y = int(getattr(cont.spatial, "meshY", 1))
            print(f"PE: meshX={pe_mesh_x}, meshY={pe_mesh_y}")
            pe_found = True
        elif cont.name == "PEy":
            pey_mesh_x = int(getattr(cont.spatial, "meshX", 1))
            pey_mesh_y = int(getattr(cont.spatial, "meshY", 1))
            print(f"PEy: meshX={pey_mesh_x}, meshY={pey_mesh_y}")
    if not pe_found:
        print("No container named 'PE' found in architecture.")
    else:
        mesh_x = int(pe_mesh_x) * int(pey_mesh_x or 1)
        mesh_y = int(pe_mesh_y) * int(pey_mesh_y or 1)
        print(f"Effective: meshX={mesh_x}, meshY={mesh_y}, N_PE={mesh_x * mesh_y}")

    # ------------------------------------------------------------------
    # 2) Problem: X/Y/Z instance parameters
    # ------------------------------------------------------------------
    print("\n=== Problem instance (X/Y/Z) ===")
    inst = spec.problem.instance
    for dim in ["X", "Y", "Z"]:
        if dim in inst:
            print(f"{dim} = {inst[dim]}")
        else:
            print(f"{dim} not found in problem.instance")

    # ------------------------------------------------------------------
    # 3) ERT: per-component unit energy
    # ------------------------------------------------------------------
    print("\n=== ERT component action energies ===")
    if spec.ERT.isempty():
        print("ERT is empty (no tables).")
    else:
        for table in spec.ERT.tables:
            print(f"Component: {table.name}")
            for action in table.actions:
                print(f"  action={action.name}, energy={action.energy}")


if __name__ == "__main__":
    main()
