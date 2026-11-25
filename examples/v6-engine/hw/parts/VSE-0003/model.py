# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0003 Wrist pin: CadQuery 2.8, part_local frame, placed in six occurrences. Axis along Y, centred on the origin."""

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf

KERNEL = "cadquery"
PARAMS = {"d": 5.996, "length": 20.0, "bore_d": 3.0}  # 6 g6 upper deviation -0.004; the piston bore is 6 H7


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    pin = cq.Workplane("XZ").circle(p["d"] / 2.0).circle(p["bore_d"] / 2.0).extrude(p["length"] / 2.0, both=True)  # tube: a printed solid pin is weak and the joint needs a bore
    return pin.faces("|Y").chamfer(0.3).rotate((0, 0, 0), (0, 0, 1), 90).val()  # pin axis X


def _witness(face: cq.Face) -> dict:
    """A final-surface witness: the centre of the face's largest mesh triangle, projected onto the face."""
    vertices, triangles = face.tessellate(0.01, 0.2)
    a, b, c = max(((vertices[i], vertices[j], vertices[k]) for i, j, k in triangles),
                  key=lambda t: (t[1] - t[0]).cross(t[2] - t[0]).Length)
    near = GeomAPI_ProjectPointOnSurf(((a + b + c) * (1.0 / 3.0)).toPnt(), BRep_Tool.Surface_s(face.wrapped)).NearestPoint()
    point = cq.Vector(near.X(), near.Y(), near.Z())
    kind = {"PLANE": "plane", "CYLINDER": "cylinder", "CONE": "cone"}.get(face.geomType(), "other")
    return {"support_type": kind, "point_mm": list(point.toTuple()), "normal": list(face.normalAt(point).normalized().toTuple())}


def drawing_features(params: dict, shape: cq.Shape) -> dict:
    """The outer journal, the lumen, both end faces and the four edge chamfers."""
    p = {**PARAMS, **params}
    named: dict[str, tuple[str, list]] = {}
    for face in shape.Faces():
        patch = _witness(face)
        if patch["support_type"] == "cylinder":
            radius = BRepAdaptor_Surface(face.wrapped).Cylinder().Radius()
            name, kind = ("journal", "shaft") if abs(radius - p["d"] / 2.0) < 1e-6 else ("lumen", "bore")
        elif patch["support_type"] == "cone":
            name, kind = "edge_chamfers", "chamfer"
        else:
            name, kind = ("end_face_a" if patch["point_mm"][0] < 0 else "end_face_b"), "plane_face"
        named.setdefault(name, (kind, []))[1].append(patch)
    return {name: {"kind": kind, "frame": "part_local", "patches": patches} for name, (kind, patches) in named.items()}
