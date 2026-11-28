# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "jsonschema>=4.23", "pyyaml>=6.0", "referencing>=0.28.4"]
# ///
"""Print stop_reaction.overlap_depth of the benchhand-013 insert in each of its two collar crops as JSON.

The crops are cad-motion's fixtures (skills/cad-motion/tests/fixtures/benchhand013), with the placement
round-off of the pair measurer in them. Usage: uv run --script overlap_depth_probe.py FIXTURE_DIR
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS / "cad-analysis" / "scripts"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    def read(path: Path):
        shape = TopoDS_Shape()
        if not BRepTools.Read_s(shape, str(path), BRep_Builder()):
            raise SystemExit(f"cannot read {path}")
        return shape

    folder = Path(sys.argv[1])
    stop_reaction = load("cad_analysis_stop_reaction", SKILLS / "cad-analysis" / "scripts" / "stop_reaction.py")
    insert = read(folder / "insert.brep")
    print(json.dumps({name: stop_reaction.overlap_depth(insert, read(folder / f"collar_{name}.brep"))
                      for name in ("m1", "m2")}))


if __name__ == "__main__":
    main()
