"""FDM treatment of horizontal holes (axis parallel to the bed), judged by the fdm support obligation on the TEST profile.

The TEST profile has no self-supporting-hole calibration. The limits below come from its overhang (45 deg) and bridge
(5 mm) calibrations: a round hole up to the 5 mm bridge span counts as self-supporting; a teardrop or diamond top whose
flanks sit at 45 deg from vertical needs no support at any diameter. Every case plans no supports.
Geometry is real B-rep probed by form_check.probe. Millimetres, bed frame, +Z up, hole axis along Y.
"""

import math
from typing import Any

from corpus import Family, Outcome, Pair
from support import assess_printed, printed_verdict, solids

SUPPORT = "process.fdm.support@"
SELF_SUPPORTING = 4.0  # round hole diameter inside the 5 mm bridge calibration
BRIDGE = 5.0  # calibrated bridge span
BLOCK = (40.0, 30.0, 40.0)  # x, y, z of the block the hole goes through
MID = 20.0  # axis height of a mid-height hole


def _verdict(shape: Any) -> Outcome:
    return printed_verdict(assess_printed(shape), SUPPORT)


def _prism(points: list[tuple[float, float]]) -> Any:
    """A closed (x, z) polygon extruded along Y across the block, with a margin either side."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    wire = BRepBuilderAPI_MakePolygon()
    for x, z in points:
        wire.Add(gp_Pnt(x, -1, z))
    wire.Close()
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(), gp_Vec(0, BLOCK[1] + 2, 0)).Shape()


def _round(diameter: float, z: float) -> Any:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(BLOCK[0] / 2, -1, z), gp_Dir(0, 1, 0)), diameter / 2, BLOCK[1] + 2).Shape()


def _teardrop(diameter: float, z: float) -> Any:
    """The round bore plus a triangle to an apex r*sqrt(2) above the axis, tangent to the circle at 45 deg."""
    s = solids()
    r, cx = diameter / 2, BLOCK[0] / 2
    side = r / math.sqrt(2)
    return s.fuse(_round(diameter, z), _prism([(cx - side, z + side), (cx, z + r * math.sqrt(2)), (cx + side, z + side)]))


def _diamond(diameter: float, z: float) -> Any:
    cx, r = BLOCK[0] / 2, diameter / 2
    return _prism([(cx - r, z), (cx, z - r), (cx + r, z), (cx, z + r)])


def _slot(width: float, z: float) -> Any:
    """A square hole: a flat roof of `width`, the bridge case."""
    cx = BLOCK[0] / 2
    return _prism([(cx - width / 2, z), (cx + width / 2, z), (cx + width / 2, z + width), (cx - width / 2, z + width)])


def _with_hole(hole: Any) -> Outcome:
    s = solids()
    return _verdict(s.cut(s.box(0, 0, 0, *BLOCK), hole))


def small_vs_large_round(mutant: bool) -> Outcome:
    """A round bore at mid-height: within the self-supporting diameter against 20 mm."""
    solids()  # first: it raises Skip when OCP is absent
    return _with_hole(_round(20.0 if mutant else SELF_SUPPORTING, MID))


def teardrop_top(mutant: bool) -> Outcome:
    """A 20 mm bore: round (mutant) or with a 45 deg teardrop top (twin)."""
    solids()
    return _with_hole(_round(20.0, MID) if mutant else _teardrop(20.0, MID))


def diamond_top(mutant: bool) -> Outcome:
    """A 20 mm bore: round (mutant) or a diamond of the same width (twin)."""
    solids()
    return _with_hole(_round(20.0, MID) if mutant else _diamond(20.0, MID))


def bed_face(mutant: bool) -> Outcome:
    """A half-bore channel open in the bed face: 4 mm against 8 mm round."""
    solids()
    return _with_hole(_round(8.0 if mutant else SELF_SUPPORTING, 0.0))


def mid_height(mutant: bool) -> Outcome:
    """The same 4 mm and 8 mm round bores as bed_face, with the axis at mid-height."""
    solids()
    return _with_hole(_round(8.0 if mutant else SELF_SUPPORTING, MID))


def square_roof_bridge(mutant: bool) -> Outcome:
    """A square horizontal hole whose flat roof spans 4 mm (inside the bridge calibration) or 12 mm (beyond it)."""
    solids()
    return _with_hole(_slot(12.0 if mutant else BRIDGE - 1.0, MID))


FAMILY = Family(
    id="fdm_horizontal_hole", check="cad-design form_check.probe + process_checks/additive.py support", owner="cad-design",
    pairs=(
        Pair("small_vs_large_round", "20 mm round horizontal bore whose roof needs support", small_vs_large_round),
        Pair("teardrop_top", "20 mm horizontal bore left round where a teardrop top is self-supporting", teardrop_top),
        Pair("diamond_top", "20 mm horizontal bore left round where a diamond top is self-supporting", diamond_top),
        Pair("bed_face", "8 mm round channel at the bed face, past the 5 mm bridge", bed_face),
        Pair("mid_height", "8 mm round bore at mid-height, past the 5 mm bridge", mid_height),
        Pair("square_roof_bridge", "12 mm flat-roofed horizontal hole beyond the 5 mm bridge", square_roof_bridge),
    ))
