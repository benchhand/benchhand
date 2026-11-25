# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0002 Piston: CadQuery 2.8, part_local frame, placed in six occurrences.

Local frame: the wrist pin axis is the X axis through the origin; the cylinder axis is +Z with the crown at +z.
A slot through the skirt along X leaves room for the rod's small end.
"""

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from cadmodel.safe import safe_cut

KERNEL = "cadquery"
PARAMS = {
    "d": 23.6,  # 0.4 mm diametral running clearance in the 24 mm bore
    "crown_h": 10.0,  # crown face above the pin axis
    "skirt_h": 12.0,  # skirt below the pin axis
    "pin_bore_d": 6.012,  # 6 H7 upper limit; the wrist pin takes 6 g6
    "slot_w": 10.0,  # clearance for the 8 mm rod small end
    "slot_top": 6.5,  # slot height above the pin axis
    "cavity_d": 16.0,  # hollow skirt under the crown: saves print time and gives the joint a bore on the bank axis
    "cavity_top": -4.6,  # cavity ceiling, below the pin bore
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    h = p["crown_h"] + p["skirt_h"]
    body = cq.Workplane("XY").workplane(offset=-p["skirt_h"]).circle(p["d"] / 2.0).extrude(h)
    slot = cq.Workplane("XY").box(p["d"] + 2.0, p["slot_w"], p["slot_top"] + p["skirt_h"] + 1.0, centered=(True, True, False))
    body = safe_cut(body, slot.translate((0, 0, -p["skirt_h"] - 1.0)))
    cavity = cq.Workplane("XY").workplane(offset=-p["skirt_h"] - 1.0).circle(p["cavity_d"] / 2.0).extrude(p["cavity_top"] + p["skirt_h"] + 1.0)
    body = safe_cut(body, cavity)
    bore = cq.Workplane("XZ").circle(p["pin_bore_d"] / 2.0).extrude(p["d"], both=True)
    return safe_cut(body, bore).rotate((0, 0, 0), (0, 0, 1), 90).val()  # pin axis X, rod swing plane YZ


def _witness(face: cq.Face) -> dict:
    """A final-surface witness: the centre of the face's largest mesh triangle, projected onto the face."""
    vertices, triangles = face.tessellate(0.01, 0.2)
    a, b, c = max(((vertices[i], vertices[j], vertices[k]) for i, j, k in triangles),
                  key=lambda t: (t[1] - t[0]).cross(t[2] - t[0]).Length)
    near = GeomAPI_ProjectPointOnSurf(((a + b + c) * (1.0 / 3.0)).toPnt(), BRep_Tool.Surface_s(face.wrapped)).NearestPoint()
    point = cq.Vector(near.X(), near.Y(), near.Z())
    kind = {"PLANE": "plane", "CYLINDER": "cylinder", "CONE": "cone", "SPHERE": "sphere", "TORUS": "torus"}.get(face.geomType(), "other")
    return {"support_type": kind, "point_mm": list(point.toTuple()), "normal": list(face.normalAt(point).normalized().toTuple())}


def _radius(face: cq.Face) -> float:
    return BRepAdaptor_Surface(face.wrapped).Cylinder().Radius()


def _collect(shape: cq.Shape, role) -> dict:
    """role(face, patch) -> (name, kind); faces with the same name join one locator."""
    named: dict[str, tuple[str, list]] = {}
    for face in shape.Faces():
        patch = _witness(face)
        name, kind = role(face, patch)
        named.setdefault(name, (kind, []))[1].append(patch)
    return {name: {"kind": kind, "frame": "part_local", "patches": patches} for name, (kind, patches) in named.items()}


def drawing_features(params: dict, shape: cq.Shape) -> dict:
    """The skirt wall, the pin bore and cavity, the crown, skirt end, slot walls and slot top, and the cavity ceiling."""
    p = {**PARAMS, **params}

    def role(face: cq.Face, patch: dict) -> tuple[str, str]:
        x, y, z = patch["point_mm"]
        if patch["support_type"] == "cylinder":
            r = _radius(face)
            for name, radius, kind in (("skirt_wall", p["d"] / 2.0, "shaft"), ("pin_bore", p["pin_bore_d"] / 2.0, "bore"),
                                       ("cavity_wall", p["cavity_d"] / 2.0, "bore")):
                if abs(r - radius) < 1e-6:
                    return name, kind
            raise ValueError(f"unexpected cylinder r={r}")
        for name, level in (("crown", p["crown_h"]), ("slot_top", p["slot_top"]), ("skirt_end", -p["skirt_h"]), ("cavity_ceiling", p["cavity_top"])):
            if abs(z - level) < 1e-6 and abs(patch["normal"][2]) > 0.99:
                return name, "plane_face"
        return ("slot_wall_pos" if x > 0 else "slot_wall_neg"), "plane_face"

    return _collect(shape, role)
