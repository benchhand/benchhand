# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0"]
# ///
"""Print pose.interference readings of the benchhand-013 heat-set insert pair as JSON.

The fixtures are the GHA-0107 insert and its GHA-0021 collar cropped to a box around it, each crop in
the insert's frame at one of the two mirror-image placements @insert-m1 and @insert-m2 the pair
measurer used: the round-off of those placements is in the crops. Each crop is measured at identity
and at offsets of b along each axis, twice over, as `{name: [[distance, volume], ...]}`.

Usage: uv run --script overlap_probe.py FIXTURE_DIR
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path

POSE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pose.py"
_spec = importlib.util.spec_from_file_location("cad_motion_pose", POSE_PATH)
assert _spec and _spec.loader
pose = importlib.util.module_from_spec(_spec)
sys.modules["cad_motion_pose"] = pose
_spec.loader.exec_module(pose)

OFFSETS_MM = (-1e-6, -3e-7, -1e-7, 1e-7, 3e-7, 1e-6)


def read_brep(path: Path):
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, str(path), BRep_Builder()):
        raise SystemExit(f"cannot read {path}")
    return shape


def main() -> None:
    folder = Path(sys.argv[1])
    insert = read_brep(folder / "insert.brep")
    identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    readings: dict[str, list[list[float]]] = {}
    for name in ("m1", "m2"):
        collar = read_brep(folder / f"collar_{name}.brep")
        placements = [(0.0, 0.0, 0.0)] + [tuple(d if i == axis else 0.0 for i in range(3))
                                          for axis, d in itertools.product(range(3), OFFSETS_MM)]
        readings[name] = [list(pose.interference(insert, pose.placed(collar, pose.Rigid(identity, t))))
                          for t in placements for _ in range(2)]
    print(json.dumps(readings))


if __name__ == "__main__":
    main()
