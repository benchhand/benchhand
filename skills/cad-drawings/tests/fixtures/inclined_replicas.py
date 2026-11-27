# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Synthetic replicas of the units that drawing intent must keep drawing as it does today.

Nothing here is copied from a real unit; the shapes follow the descriptions of the designs they stand for.
Each replica is a ``model.py`` (CadQuery, with its ``drawing_features`` hook) and the ``drawing:`` block of a
part record. ``install`` writes them over one part of a migrated copy of the lamp-arm template.

``collar``: the shape of GHA-0001 v3, a block with a through collar bore along z (datum axis A) on a flat base
(datum plane B) and two heat-set insert bores, each blind with a flat floor, 15 deg off z about y, entering
through a boss face square to their own axis. ``INSERTS`` picks how the drawing names the inclined bores and
boss faces: ``surface`` (controlled-model surfaces, the v3 workaround) or ``native`` (a bore and a plane_face, as
the unit's pre-workaround drawing names them).

``crank``: a frame-aligned shaft with every feature on the x axis or a plane normal to it: a main journal
along x with a blind coupling bore, a disc, and an offset crank pin, the class of VSE-0005 (a crankshaft).
ASSUMPTION: the replica follows the shape class of that unit, not its measurements.
"""

from __future__ import annotations

from pathlib import Path

SIDE_TILT_DEG = 15.0

_HEADER = '''# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Synthetic replica model for a drawing-intent test; see tests/fixtures/inclined_replicas.py."""

import math

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf

KERNEL = "cadquery"
PARAMS = {}

SUPPORT = {"PLANE": "plane", "CYLINDER": "cylinder", "CONE": "cone", "SPHERE": "sphere", "TORUS": "torus"}


def _witness(face):
    vertices, triangles = face.tessellate(0.01, 0.2)
    a, b, c = max(((vertices[i], vertices[j], vertices[k]) for i, j, k in triangles),
                  key=lambda t: (t[1] - t[0]).cross(t[2] - t[0]).Length)
    centre = (a + b + c) * (1.0 / 3.0)
    near = GeomAPI_ProjectPointOnSurf(centre.toPnt(), BRep_Tool.Surface_s(face.wrapped)).NearestPoint()
    point = cq.Vector(near.X(), near.Y(), near.Z())
    return {"support_type": SUPPORT.get(face.geomType(), "other"), "point_mm": list(point.toTuple()),
            "normal": list(face.normalAt(point).normalized().toTuple())}


def _located(features, kinds):
    return {name: {"kind": kinds[name], "frame": "assembly_zero", "patches": patches}
            for name, patches in features.items()}
'''

_COLLAR = '''
INSERTS = "{inserts}"
TILT = math.radians({tilt})
KINDS = {{"collar_bore": "bore", "base": "plane_face", "top": "plane_face", "end_plus": "plane_face",
         "end_minus": "plane_face", "side_plus": "plane_face", "side_minus": "plane_face"}}
for _side in ("plus", "minus"):
    KINDS["insert_bore_" + _side] = "surface" if INSERTS == "surface" else "bore"
    KINDS["boss_face_" + _side] = "surface" if INSERTS == "surface" else "plane_face"


def _axis(sign):
    return cq.Vector(sign * math.sin(TILT), 0.0, math.cos(TILT))


def build(params):
    shape = cq.Workplane("XY").box(50, 20, 30, centered=(True, True, False)).val()
    shape = shape.cut(cq.Solid.makeCylinder(4.0, 30.0, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1)))
    for sign in (1, -1):
        axis = _axis(sign)
        boss = cq.Vector(sign * 12.0, 0.0, 30.0)
        slope = cq.Vector(sign * math.cos(TILT), 0.0, -math.sin(TILT))
        tool = cq.Workplane(cq.Plane(boss, (0, 1, 0), axis)).rect(80, 80).extrude(40).val()
        shape = shape.cut(tool)
        shape = shape.cut(cq.Solid.makeCylinder(2.85, 8.1, boss + slope * 5.5, axis * -1.0))
    return shape


def drawing_features(params, shape):
    features = {{}}
    for face in shape.Faces():
        patch = _witness(face)
        point, normal = cq.Vector(*patch["point_mm"]), cq.Vector(*patch["normal"])
        name = None
        if patch["support_type"] == "cylinder":
            radius = BRepAdaptor_Surface(face.wrapped).Cylinder().Radius()
            name = "collar_bore" if abs(radius - 4.0) < 1e-6 else (
                "insert_bore_plus" if point.x > 0 else "insert_bore_minus")
        elif patch["support_type"] == "plane":
            if abs(normal.z) > 0.999:
                name = "base" if point.z < 1.0 else "top"
            elif abs(normal.x) > 0.999:
                name = "end_plus" if normal.x > 0 else "end_minus"
            elif abs(normal.y) > 0.999:
                name = "side_plus" if normal.y > 0 else "side_minus"
            else:
                side = "plus" if normal.x > 0 else "minus"
                boss = cq.Vector((12.0 if side == "plus" else -12.0), 0.0, 30.0)
                ramp = abs((point - boss).dot(_axis(1 if side == "plus" else -1))) < 1e-6
                name = ("boss_face_" if ramp else "insert_bore_") + side
        if name is not None:
            features.setdefault(name, []).append(patch)
    return _located(features, KINDS)
'''


_CRANK = '''
KINDS = {"main_journal": "shaft", "disc": "shaft", "crank_pin": "shaft", "coupling_bore": "bore"}


def build(params):
    shape = cq.Solid.makeCylinder(7.0, 70.0, cq.Vector(-60, 0, 0), cq.Vector(1, 0, 0))
    shape = shape.fuse(cq.Solid.makeCylinder(20.0, 14.0, cq.Vector(10, 0, 0), cq.Vector(1, 0, 0)))
    shape = shape.fuse(cq.Solid.makeCylinder(5.0, 22.0, cq.Vector(24, 15, 0), cq.Vector(1, 0, 0)))
    return shape.cut(cq.Solid.makeCylinder(4.0, 20.0, cq.Vector(-60, 0, 0), cq.Vector(1, 0, 0))).clean()


def drawing_features(params, shape):
    features, kinds = {}, dict(KINDS)
    for face in shape.Faces():
        patch = _witness(face)
        point, normal = cq.Vector(*patch["point_mm"]), cq.Vector(*patch["normal"])
        name = None
        if patch["support_type"] == "cylinder":
            radius = BRepAdaptor_Surface(face.wrapped).Cylinder().Radius()
            name = {7.0: "main_journal", 20.0: "disc", 5.0: "crank_pin", 4.0: "coupling_bore"}[round(radius, 6)]
        elif abs(normal.x) > 0.999:
            if abs(point.x + 40.0) < 1e-6:
                name = "coupling_floor"
            else:
                name = "face_x_%s%d" % ("p" if point.x > 0 else "m", round(abs(point.x)))
            kinds[name] = "plane_face"
        if name is not None:
            features.setdefault(name, []).append(patch)
    return _located(features, kinds)
'''

CRANK_FEATURES = ("main_journal", "disc", "crank_pin", "coupling_bore", "coupling_floor", "face_x_m60", "face_x_p10",
                  "face_x_p24", "face_x_p46")
COLLAR_PLANES = ("collar_bore", "base", "top", "end_plus", "end_minus", "side_plus", "side_minus")

_CRANK_DRAWING = '''drawing:
  type: detail
  tolerance_policy: machining
  datum_features:
    A: {feature: face_x_m60, role: plane}
    B: {feature: main_journal, role: axis}
  coordinate_frame:
    origin: {intersection: [A, B]}
    x: {normal_to: A, toward: face_x_p46}
    y: {toward: crank_pin}
  views:
    - {id: elevation, kind: orthographic, perpendicular_to: B}
    - {id: end, kind: orthographic, along_axis: B, toward: face_x_p46}
    - {id: section_bore, kind: section, normal_axis: z, through: coupling_bore}
  features:
%s  notes:
    - {kind: finish, text: "Deburr; no sharp edges."}
  edges: {external: [-0.3, 0.0], internal: [0.0, 0.3]}
  surface_texture: {parameter: Ra, limit_um: 1.6, process: removal_required}
  sheet: {sizes: [A4, A3], scale: auto}
'''

_COLLAR_DRAWING = '''drawing:
  type: detail
  tolerance_policy: machining
  datum_features:
    A: {feature: collar_bore, role: axis}
    B: {feature: base, role: plane}
  coordinate_frame:
    origin: {intersection: [A, B]}
    z: {parallel_to: A, toward: top}
    x: {toward: end_plus}
  views:
    - {id: front, kind: orthographic, normal_to: side_plus}
    - {id: plan, kind: orthographic, normal_to: top}
  features:
%s  notes:
    - {kind: finish, text: "Deburr; no sharp edges."}
  edges: {external: [-0.3, 0.0], internal: [0.0, 0.3]}
  surface_texture: {parameter: Ra, limit_um: 1.6, process: removal_required}
  sheet: {sizes: [A4, A3], scale: auto}
'''


def _features(general, controlled=()):
    rows = [f"    {name}: {{size: general}}\n" for name in general]
    rows += [f"    {name}: {{profile: controlled_model, profile_tolerance_mm: 0.2}}\n" for name in controlled]
    return "".join(rows)


def collar(inserts: str) -> tuple[str, str]:
    """(model.py, drawing block) of the collar replica; ``inserts`` is ``surface`` or ``native``."""
    inclined = [f"{kind}_{side}" for kind in ("insert_bore", "boss_face") for side in ("plus", "minus")]
    features = (_features(COLLAR_PLANES, inclined) if inserts == "surface" else _features(COLLAR_PLANES + tuple(inclined)))
    drawing = _COLLAR_DRAWING % features
    if inserts == "surface":   # the v3 workaround: each inclined bore is its own datum axis
        drawing = drawing.replace("    B: {feature: base, role: plane}\n", "    B: {feature: base, role: plane}\n"
                                  "    D: {feature: insert_bore_plus, role: axis}\n"
                                  "    E: {feature: insert_bore_minus, role: axis}\n")
        requests = "".join(
            f"    - {{id: insert_{side}_{what}, operation: {what}, features: [insert_bore_{side}], "
            f"tolerance: {{deviation_mm: [-0.1, 0.1]}}}}\n"
            for side in ("plus", "minus") for what in ("diameter", "length"))
        drawing = drawing.replace("  notes:\n", "  dimensions:\n" + requests + "  notes:\n")
    return (_HEADER + _COLLAR.format(inserts=inserts, tilt=SIDE_TILT_DEG), drawing)


def crank() -> tuple[str, str]:
    return (_HEADER + _CRANK, _CRANK_DRAWING % _features(CRANK_FEATURES))


def install(hw: Path, pn: str, replica: tuple[str, str]) -> None:
    """Overwrite ``pn``'s model and drawing block in a migrated template copy; its other record text stays."""
    model, drawing = replica
    folder = hw / "parts" / pn
    (folder / "model.py").write_text(model, encoding="utf-8")
    text = (folder / "part.yaml").read_text(encoding="utf-8")
    start, end = text.index("\ndrawing:\n") + 1, text.index("\nintent:\n") + 1
    (folder / "part.yaml").write_text(text[:start] + drawing + text[end:], encoding="utf-8")
    interfaces = hw / "interfaces.yaml"   # the template's fits bind features of the pin this replica replaces
    kept = [line for line in interfaces.read_text(encoding="utf-8").splitlines(keepends=True)
            if not line.startswith(("      press_fit:", "      running_fit:", "      retaining_ring:"))]
    interfaces.write_text("".join(kept), encoding="utf-8")
