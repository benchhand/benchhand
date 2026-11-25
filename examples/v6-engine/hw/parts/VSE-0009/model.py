# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0009 Cam pulley: CadQuery 2.8, part_local frame, one occurrence per camshaft stub.

The axis runs along X through the origin. A 40-tooth GT2 pulley (2 mm pitch, pitch diameter 25.46 mm) with flanges,
bored 6 H7 for the 6 mm camshaft stub. Twice the 20 teeth of the crank pulley, so the cam turns once per two crank turns.
"""

import math

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "teeth": 40,
    "pitch_mm": 2.0,
    "width": 8.0,
    "flange_t": 1.0,
    "flange_extra_r": 1.5,
    "tooth_depth": 0.75,  # GT2 tooth height
    "bore_d": 6.006,  # 6 H7 mid-limit for the 6 mm camshaft stub
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    pitch_r = p["teeth"] * p["pitch_mm"] / math.pi / 2.0
    root_r = pitch_r - 0.254 - p["tooth_depth"]  # GT2 pitch line sits 0.254 mm outside the tooth tips
    hub_w = p["width"] - 2.0 * p["flange_t"]
    x0 = -p["width"] / 2.0
    body = cq.Workplane("YZ").workplane(offset=x0 + p["flange_t"]).circle(pitch_r - 0.254).extrude(hub_w)
    for i in range(p["teeth"]):  # one groove per tooth
        a = 2.0 * math.pi * i / p["teeth"]
        r = root_r + p["tooth_depth"] / 2.0 + 0.4
        groove = cq.Workplane("YZ").workplane(offset=x0 + p["flange_t"]).center(r * math.cos(a), r * math.sin(a)).circle(0.55).extrude(hub_w)
        body = safe_cut(body, groove)
    for off in (x0, p["width"] / 2.0 - p["flange_t"]):
        body = safe_fuse(body, cq.Workplane("YZ").workplane(offset=off).circle(pitch_r + p["flange_extra_r"]).extrude(p["flange_t"]))
    bore = cq.Workplane("YZ").workplane(offset=x0 - 1.0).circle(p["bore_d"] / 2.0).extrude(p["width"] + 2.0)
    return safe_cut(body, bore).val()


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
    """The flange rims, the tooth root hub, the 20 or 40 tooth grooves, the bore and the four flange faces."""
    p = {**PARAMS, **params}
    hub_x = p["width"] / 2.0 - p["flange_t"]

    def role(face: cq.Face, patch: dict) -> tuple[str, str]:
        if patch["support_type"] == "cylinder":
            r = _radius(face)
            if abs(r - p["bore_d"] / 2.0) < 1e-6:
                return "bore", "bore"
            if abs(r - 0.55) < 1e-6:
                return "tooth_grooves", "bore"
            return ("flange_rim", "shaft") if r > p["teeth"] * p["pitch_mm"] / 6.2832 else ("tooth_root", "shaft")
        x = patch["point_mm"][0]
        side = "a" if x < 0 else "b"
        return (f"hub_face_{side}" if abs(abs(x) - hub_x) < 1e-6 else f"flange_face_{side}"), "plane_face"

    return _collect(shape, role)
