# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0"]
# ///
"""Write the latch stop test geometry as STEP AP242: an OCP copy of the small-mechanism latch lever.

Usage: uv run --script stop_fixture.py OUT_DIR [LATCHED_GAP_MM [VARIANT]]

Every part is modelled in place (assembly zero, identity placement), millimetres. The pivot axis is the Y axis
through the origin; the lever turns about -Y from 0 deg (latched, lying along +X) to 75 deg (open, pointing up):
  TST-0301 bracket  a base plate, a latched stop block under the lever (x 7..13, its top face at z -3) and an
                    open stop block above it (a box standing on the lever's top face at 75 deg, x 0..7 along the
                    lever), joined by a post
  TST-0302 lever    x 0..80, y -4..4, z -3..3

The latched stop is touched across arms 7..13 mm from the axis, the open stop across arms 0..7 mm, as in the
latch lever whose hand-estimated stop reaction (240 N, mid-span) hid that the nearest edge carries 2400/7 N.

LATCHED_GAP_MM (default 0) lowers the latched stop's top face by that much, so the lever at 0 deg stops short of it.

VARIANT changes one thing:
  ring   adds TST-0303, a small ring at the lever's tip, for a second joint
  disk   the latched stop is a disc of radius 10 centred at x 10, y 2 whose rim touches the axis line at x 0, so its
         nearest arm is exactly 0, at a rim point that is no vertex and falls between the 64 samples of a sampled
         outline. Its lever starts at x -2, past the axis line
  disk3  the same disc centred at x 13, so its rim is nearest the axis line at x 3, at the rim point (3, 2)
  round  the lever ends in a cylinder of radius 10 centred at x 70, so its farthest point from the axis, at x 80,
         is on an arc between sampled points

OUT_DIR/parts/<PN>.step.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

POSE_PATH = Path(__file__).resolve().parents[3] / "cad-motion" / "scripts" / "pose.py"
_spec = importlib.util.spec_from_file_location("cad_motion_pose", POSE_PATH)
assert _spec and _spec.loader
pose = importlib.util.module_from_spec(_spec)
sys.modules["cad_motion_pose"] = pose
_spec.loader.exec_module(pose)

TITLES = {"TST-0301": "Bracket", "TST-0302": "Lever", "TST-0303": "Ring"}
HALF_SAMPLE = math.pi / 128  # half the step of 64 samples round a circle
OPEN_DEGREES = 75.0


def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def cylinder(x: float, y: float, z: float, axis: str, radius: float, length: float):
    """A cylinder from (x, y, z) along +Z or +Y with its seam turned by HALF_SAMPLE, so the rim point that matters
    (nearest -X for axis z, farthest +X for axis y) is half a step from a sample."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    cos, sin = math.cos(HALF_SAMPLE), math.sin(HALF_SAMPLE)
    along, seam = (gp_Dir(0, 0, 1), gp_Dir(cos, sin, 0)) if axis == "z" else (gp_Dir(0, 1, 0), gp_Dir(cos, 0, sin))
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), along, seam), radius, length).Shape()


def turned(shape, degrees: float):
    """`shape` with its geometry rotated by `degrees` about -Y through the origin."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    rigid = pose.Rigid.rotation((0.0, -1.0, 0.0), degrees, (0.0, 0.0, 0.0))
    return BRepBuilderAPI_Transform(shape, pose.to_trsf(rigid), True).Shape()


def fused(*shapes):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy

    result = shapes[0]
    for other in shapes[1:]:
        result = BRepAlgoAPI_Fuse(result, other).Shape()
    return BRepBuilderAPI_Copy(result).Shape()


def shapes(latched_gap: float = 0.0, variant: str = "") -> dict[str, object]:
    base = box(-30, -14, -16, 30, 14, -8)
    if variant in ("disk", "disk3"):
        latched_stop = cylinder(10 if variant == "disk" else 13, 2, -8, "z", 10, 5 - latched_gap)
    else:
        latched_stop = box(7, -4, -8, 13, 4, -3 - latched_gap)  # top face at z -3: the lever's underside rests on it at 0 deg
    open_stop = turned(box(0, -4, 3, 7, 4, 9), OPEN_DEGREES)  # its face lies in the lever's top plane at 75 deg
    post = box(-12, -4, -8, -4, 4, 10)
    lever = box(-2 if variant == "disk" else 0, -4, -3, 80, 4, 3)  # the disc variant's lever runs past the axis line
    if variant == "round":
        lever = fused(box(0, -4, -3, 70, 4, 3), cylinder(70, -4, 0, "y", 10, 8))
    parts = {"TST-0301": fused(base, latched_stop, open_stop, post), "TST-0302": lever}
    if variant == "ring":
        parts["TST-0303"] = box(84, -3, -3, 90, 3, 3)
    return parts


def main(out: Path, latched_gap: float = 0.0, variant: str = "") -> int:
    (out / "parts").mkdir(parents=True, exist_ok=True)
    for pn, shape in shapes(latched_gap, variant).items():
        pose.write_part_step(out / "parts" / f"{pn}.step", pose.Part(pn, f"{pn} {TITLES[pn]}", shape))
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3, 4):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 0.0, sys.argv[3] if len(sys.argv) > 3 else ""))
