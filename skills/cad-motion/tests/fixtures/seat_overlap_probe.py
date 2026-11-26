# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0"]
# ///
"""Print cad-motion pose.diametral_overlap_mm of three seats as JSON.

Usage: uv run --script seat_overlap_probe.py

plain: a 40 x 40 x 10 block with a 22 mm bore and a 22.6 mm shaft: 0.6 mm diametral interference.
ribs:  a 23 mm bore with six 1.2 mm wide crush ribs whose flat tips stand 10.6 mm off the axis, and a
       22 mm shaft: the bore clears it, the ribs overlap it 0.4 mm radially, 0.8 mm on the diameter.
clear: the 22 mm shaft in the 23 mm bore without ribs: nothing shared.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("cad_motion_pose", Path(__file__).resolve().parents[2] / "scripts" / "pose.py")
assert SPEC and SPEC.loader
pose = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pose
SPEC.loader.exec_module(pose)

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf  # noqa: E402


def cylinder(diameter: float, z: float, height: float):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), diameter / 2, height).Shape()


def block_with_bore(diameter: float):
    return BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(gp_Pnt(-20, -20, 0), 40, 40, 10).Shape(), cylinder(diameter, -1, 12)).Shape()


def ribbed_bore():
    body = block_with_bore(23.0)
    for number in range(6):
        turn = gp_Trsf()
        turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(60 * number))
        rib = BRepPrimAPI_MakeBox(gp_Pnt(10.6, -0.6, 0), 1.5, 1.2, 10).Shape()
        body = BRepAlgoAPI_Fuse(body, BRepBuilderAPI_Transform(rib, turn, True).Shape()).Shape()
    return body


shaft = cylinder(22.0, 0, 10)
print(json.dumps({"plain": pose.diametral_overlap_mm(block_with_bore(22.0), cylinder(22.6, 0, 10)),
                  "ribs": pose.diametral_overlap_mm(ribbed_bore(), shaft),
                  "clear": pose.diametral_overlap_mm(block_with_bore(23.0), shaft)}))
