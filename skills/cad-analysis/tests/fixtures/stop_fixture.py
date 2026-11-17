# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0"]
# ///
"""Write the latch stop test geometry as STEP AP242: an OCP copy of the small-mechanism latch lever.

Usage: uv run --script stop_fixture.py OUT_DIR

Every part is modelled in place (assembly zero, identity placement), millimetres. The pivot axis is the Y axis
through the origin; the lever turns about -Y from 0 deg (latched, lying along +X) to 75 deg (open, pointing up):
  TST-0301 bracket  a base plate, a latched stop block under the lever (x 7..13, its top face at z -3) and an
                    open stop block above it (a box standing on the lever's top face at 75 deg, x 0..7 along the
                    lever), joined by a post
  TST-0302 lever    x 0..80, y -4..4, z -3..3

The latched stop is touched across arms 7..13 mm from the axis, the open stop across arms 0..7 mm, as in the
latch lever whose hand-estimated stop reaction (240 N, mid-span) hid that the nearest edge carries 2400/7 N.

OUT_DIR/parts/<PN>.step.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

POSE_PATH = Path(__file__).resolve().parents[3] / "cad-motion" / "scripts" / "pose.py"
_spec = importlib.util.spec_from_file_location("cad_motion_pose", POSE_PATH)
assert _spec and _spec.loader
pose = importlib.util.module_from_spec(_spec)
sys.modules["cad_motion_pose"] = pose
_spec.loader.exec_module(pose)

TITLES = {"TST-0301": "Bracket", "TST-0302": "Lever"}
OPEN_DEGREES = 75.0


def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


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


def shapes() -> dict[str, object]:
    base = box(-30, -14, -16, 30, 14, -8)
    latched_stop = box(7, -4, -8, 13, 4, -3)  # top face at z -3: the lever's underside rests on it at 0 deg
    open_stop = turned(box(0, -4, 3, 7, 4, 9), OPEN_DEGREES)  # its face lies in the lever's top plane at 75 deg
    post = box(-12, -4, -8, -4, 4, 10)
    return {"TST-0301": fused(base, latched_stop, open_stop, post), "TST-0302": box(0, -4, -3, 80, 4, 3)}


def main(out: Path) -> int:
    (out / "parts").mkdir(parents=True, exist_ok=True)
    for pn, shape in shapes().items():
        pose.write_part_step(out / "parts" / f"{pn}.step", pose.Part(pn, f"{pn} {TITLES[pn]}", shape))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
