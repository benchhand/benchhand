# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0004 Connecting rod: CadQuery 2.8, part_local frame, placed in six occurrences.

Local frame: the small end (wrist pin) axis is the Y axis through the origin; the big end (crank pin) axis is
parallel to it at z = -rod_length. The rod is a flat web 8 mm thick along Y with an I-section lightening on both faces.
"""

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "rod_length": 48.0,  # centre to centre
    "thickness": 8.0,
    "small_r": 6.0,
    "big_r": 10.0,
    "small_bore_d": 6.1,  # 0.1 mm over the 6 g6 pin: the rod turns on the pin
    "big_bore_d": 12.2,  # 12 mm crank pin, 0.2 mm running clearance for FDM
    "web_w": 7.0,
    "pocket_depth": 1.5,
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    t, length = p["thickness"], p["rod_length"]
    small = cq.Workplane("XZ").circle(p["small_r"]).extrude(t / 2.0, both=True)
    big = cq.Workplane("XZ").center(0, -length).circle(p["big_r"]).extrude(t / 2.0, both=True)
    web = cq.Workplane("XY").box(p["web_w"], t, length, centered=(True, True, False)).translate((0, 0, -length))
    body = safe_fuse(small, big, web)
    for sign in (1, -1):
        pocket = cq.Workplane("XY").box(3.0, p["pocket_depth"], length - p["small_r"] - p["big_r"] - 6.0, centered=(True, False, False))
        pocket = pocket.translate((0, t / 2.0 - p["pocket_depth"] if sign > 0 else -t / 2.0, -length + p["big_r"] + 3.0))
        body = safe_cut(body, pocket)
    body = safe_cut(body, cq.Workplane("XZ").circle(p["small_bore_d"] / 2.0).extrude(t, both=True))
    body = safe_cut(body, cq.Workplane("XZ").center(0, -length).circle(p["big_bore_d"] / 2.0).extrude(t, both=True))
    return body.rotate((0, 0, 0), (0, 0, 1), 90).val()  # wrist axis X, rod swing plane YZ


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
    """Both rod faces, the web sides, the two rims, the two bores and the pocket floors, sides and ends."""
    p = {**PARAMS, **params}
    floor = p["thickness"] / 2.0 - p["pocket_depth"]

    def role(face: cq.Face, patch: dict) -> tuple[str, str]:
        x, y, z = patch["point_mm"]
        if patch["support_type"] == "cylinder":
            r = _radius(face)
            for name, radius, kind in (("big_end_rim", p["big_r"], "shaft"), ("small_end_rim", p["small_r"], "shaft"),
                                       ("big_bore", p["big_bore_d"] / 2.0, "bore"), ("small_bore", p["small_bore_d"] / 2.0, "bore")):
                if abs(r - radius) < 1e-6:
                    return name, kind
            raise ValueError(f"unexpected cylinder r={r}")
        sx, sy = ("pos" if x > 0 else "neg"), ("pos" if y > 0 else "neg")
        if abs(abs(x) - p["thickness"] / 2.0) < 1e-6:
            return f"rod_face_{sx}", "plane_face"
        if abs(abs(x) - floor) < 1e-6:
            return f"pocket_floor_{sx}", "plane_face"
        if abs(abs(y) - p["web_w"] / 2.0) < 1e-6:
            return f"web_side_{sy}", "plane_face"
        if abs(abs(y) - 1.5) < 1e-6:
            return f"pocket_side_{sy}", "plane_face"
        return ("pocket_end_small" if z > -p["rod_length"] / 2.0 else "pocket_end_big"), "plane_face"

    return _collect(shape, role)
