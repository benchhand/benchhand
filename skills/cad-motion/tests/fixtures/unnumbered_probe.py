# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0"]
# ///
"""Write an assembly STEP with pose.write_assembly_step and read it back with pose.read_assembly_step.

Usage: uv run --script unnumbered_probe.py OUT.step
The top holds the group products made and purchased, which carry no part number. Inside them sit two
numbered parts, TST-0001 and TST-0002, and Body001, a leaf product with no part number.
stdout: {"components": [component names], "unnumbered": [leaf names]}.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

_spec = importlib.util.spec_from_file_location("cad_motion_pose", Path(__file__).resolve().parents[2] / "scripts" / "pose.py")
assert _spec is not None and _spec.loader is not None
pose = importlib.util.module_from_spec(_spec)
sys.modules["cad_motion_pose"] = pose
_spec.loader.exec_module(pose)

out = Path(sys.argv[1])
box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
parts = {pn: pose.Part(pn, name, box) for pn, name in
         (("TST-0001", "TST-0001 Base"), ("TST-0002", "TST-0002 Pin"), ("Body001", "Body001"))}
occurrences = [SimpleNamespace(id=pn, pn=pn, explicit=False) for pn in parts]
here = pose.Rigid(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0))
pose.write_assembly_step(out, "fixture v1", parts, occurrences, {pn: here for pn in parts},
                         {"TST-0001": "made", "Body001": "made", "TST-0002": "purchased"})
step = pose.read_assembly_step(out)
json.dump({"components": sorted(c.name for c in step.components), "unnumbered": list(step.unnumbered)}, sys.stdout)
