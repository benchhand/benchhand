# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0001 Crankcase and V block: CadQuery 2.8, 60 degree V, three cylinders per bank.

Zero pose, assembly coordinates: the crank axis is the X axis through the origin; the V opens toward +Z. Each bank
axis n = (0, -sin b, cos b) is the +Z axis turned about +X by the bank angle b = -30 degrees (right bank, odd
cylinders) or +30 degrees (left bank, even). Crank throw k sits at x = (k - 1) * pitch; the right bank's cylinder is
5.25 mm toward -X of its throw and the left bank's 5.25 mm toward +X, so the two rods of a throw sit side by side.
"""

import math

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "bank_deg": 30.0,
    "pitch": 36.0,  # distance between crank throws along X
    "rod_offset": 5.25,  # rod centre to throw centre
    "bore_d": 24.0,
    "deck_h": 76.0,  # crank axis to deck face along the bank axis
    "deck_w": 42.0,
    "case_len": 120.0,
    "case_y": 64.0,
    "case_z": 40.0,
    "window_d": 56.0,  # crank and rod clearance, along X
    "window_len": 100.0,  # 2 mm axial thrust clearance each side of the crank webs
    "main_bore_d": 14.0,  # main bearing seat, 14 H7
    "seat_len": 12.0,
}


def _bank(p: dict, angle: float) -> cq.Workplane:
    deck = cq.Workplane("XY").box(p["case_len"], p["deck_w"], p["deck_h"], centered=(True, True, False))
    return deck.rotate((0, 0, 0), (1, 0, 0), angle)


def _cyl_x(r: float, x0: float, x1: float) -> cq.Workplane:
    return cq.Workplane(obj=cq.Solid.makeCylinder(r, x1 - x0, cq.Vector(x0, 0, 0), cq.Vector(1, 0, 0)))


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    b = p["bank_deg"]
    body = cq.Workplane("XY").box(p["case_len"], p["case_y"], p["case_z"], centered=(True, True, True))
    for angle in (-b, b):
        body = safe_fuse(body, _bank(p, angle))
    half = p["case_len"] / 2.0
    body = safe_cut(body, _cyl_x(p["window_d"] / 2.0, -p["window_len"] / 2.0, p["window_len"] / 2.0))
    for side, angle in ((-1, -b), (1, b)):
        for k in range(3):
            x = (k - 1) * p["pitch"] + side * p["rod_offset"]
            bore = cq.Workplane("XY").circle(p["bore_d"] / 2.0).extrude(p["deck_h"] + 5.0).translate((x, 0, 0))
            body = safe_cut(body, bore.rotate((0, 0, 0), (1, 0, 0), angle))
    for x0, x1 in ((-half, -half + p["seat_len"]), (half - p["seat_len"], half)):
        body = safe_cut(body, _cyl_x(p["main_bore_d"] / 2.0, x0 - 1.0, x1 + 1.0))
    return body.val()


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
    return {name: {"kind": kind, "frame": "assembly_zero", "patches": patches} for name, (kind, patches) in named.items()}


def drawing_features(params: dict, shape: cq.Shape) -> dict:
    """The block outline, the two inclined banks (deck, bore floors, sides, bores), the crank window and the main seats."""
    p = {**PARAMS, **params}
    b = math.radians(p["bank_deg"])
    axes = {"a": (0.0, math.sin(b), math.cos(b)), "b": (0.0, -math.sin(b), math.cos(b))}
    half, wl = p["case_len"] / 2.0, p["window_len"] / 2.0

    def role(face: cq.Face, patch: dict) -> tuple[str, str]:
        point, n = patch["point_mm"], patch["normal"]
        if patch["support_type"] == "cylinder":
            axis = BRepAdaptor_Surface(face.wrapped).Cylinder().Axis().Direction()
            r = _radius(face)
            if abs(r - p["main_bore_d"] / 2.0) < 1e-6:
                return "main_seat", "bore"
            if abs(r - p["window_d"] / 2.0) < 1e-6:
                return "crank_window", "bore"
            if abs(r - p["bore_d"] / 2.0) < 1e-6:
                return ("bank_a_bores" if axis.Y() * axis.Z() > 0 else "bank_b_bores"), "surface"
            raise ValueError(f"unexpected cylinder r={r}")
        if abs(n[0]) > 0.99:
            x = point[0]
            if abs(abs(x) - half) < 1e-6:
                return ("end_face_pos" if x > 0 else "end_face_neg"), "plane_face"
            return ("window_wall_pos" if x > 0 else "window_wall_neg"), "plane_face"
        if abs(n[1]) > 0.99:
            return ("case_side_pos" if point[1] > 0 else "case_side_neg"), "plane_face"
        if abs(n[2]) > 0.99:
            return "base_face", "plane_face"
        for bank, a in axes.items():
            along = n[1] * a[1] + n[2] * a[2]
            if abs(along) > 0.99:
                s = point[1] * a[1] + point[2] * a[2]
                return (f"bank_{bank}_deck" if abs(s - p["deck_h"]) < 1e-6 else f"bank_{bank}_bore_floors"), "surface"
        for bank, a in axes.items():
            if abs(n[1] * a[1] + n[2] * a[2]) < 1e-6:
                return f"bank_{bank}_side_{'inner' if abs(point[1]) < 20.0 else 'outer'}", "surface"
        raise ValueError(f"unexpected plane at {point} normal {n}")

    return _collect(shape, role)
