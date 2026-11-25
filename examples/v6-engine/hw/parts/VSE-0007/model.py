# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0007 Camshaft: CadQuery 2.8, part_local frame, placed twice (one per head). First cut.

The axis runs along X through the origin; the part is 156 mm long. Two 8 f7 journals ride in the head's 8 H7 bore. Three lobes sit under the three
cylinders (36 mm pitch). The 6 mm stub at the -X end (local x -78..-70) carries the cam pulley, so both pulleys sit on the crank nose side.
"""

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf

KERNEL = "cadquery"
PARAMS = {
    "length": 156.0,
    "journal_d": 7.987,  # 8 f7 upper deviation -0.013; the head bore is 8 H7
    "journal_x": (-50.0, -18.0, 18.0, 50.0),
    "journal_w": 8.0,
    "lobe_x": (-36.0, 0.0, 36.0),
    "lobe_w": 6.0,
    "lobe_base_r": 5.5,
    "lobe_lift": 1.5,  # the nose reaches 7 mm from the axis
    "stub_d": 6.0,
    "stub_len": 8.0,  # the pulley seat at the -X end
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    half = p["length"] / 2.0
    shaft_len = p["length"] - p["stub_len"]
    shaft = cq.Workplane("YZ").workplane(offset=-half + p["stub_len"]).circle(3.0).extrude(shaft_len)  # 6 mm core between the journals and lobes
    stub = cq.Workplane("YZ").workplane(offset=-half).circle(p["stub_d"] / 2.0).extrude(p["stub_len"])
    body = shaft.union(stub)
    for x in p["journal_x"]:
        body = body.union(cq.Workplane("YZ").workplane(offset=x - p["journal_w"] / 2.0).circle(p["journal_d"] / 2.0).extrude(p["journal_w"]))
    for x in p["lobe_x"]:
        lobe = cq.Workplane("YZ").workplane(offset=x - p["lobe_w"] / 2.0).circle(p["lobe_base_r"]).extrude(p["lobe_w"])
        nose = cq.Workplane("YZ").workplane(offset=x - p["lobe_w"] / 2.0).center(0, p["lobe_base_r"]).ellipse(p["lobe_base_r"] * 0.45, p["lobe_lift"] + 0.0).extrude(p["lobe_w"])
        body = body.union(lobe).union(nose)
    return body.val()


def _witness(face: cq.Face) -> dict:
    """A final-surface witness: the centre of the face's largest mesh triangle, projected onto the face."""
    vertices, triangles = face.tessellate(0.01, 0.2)
    a, b, c = max(((vertices[i], vertices[j], vertices[k]) for i, j, k in triangles),
                  key=lambda t: (t[1] - t[0]).cross(t[2] - t[0]).Length)
    near = GeomAPI_ProjectPointOnSurf(((a + b + c) * (1.0 / 3.0)).toPnt(), BRep_Tool.Surface_s(face.wrapped)).NearestPoint()
    point = cq.Vector(near.X(), near.Y(), near.Z())
    kind = {"PLANE": "plane", "CYLINDER": "cylinder", "CONE": "cone", "SPHERE": "sphere", "TORUS": "torus", "EXTRUSION": "extrusion"}.get(face.geomType(), "other")
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
    """The core, journals and lobe bases as one feature each, the lobe noses, and every shoulder plane by its place."""
    p = {**PARAMS, **params}
    half = p["length"] / 2.0
    levels = {"end_face_neg": -half, "end_face_pos": half}
    for i, x in enumerate(p["journal_x"], 1):
        levels[f"journal_{i}_face_a"], levels[f"journal_{i}_face_b"] = x - p["journal_w"] / 2.0, x + p["journal_w"] / 2.0
    for i, x in enumerate(p["lobe_x"], 1):
        levels[f"lobe_{i}_face_a"], levels[f"lobe_{i}_face_b"] = x - p["lobe_w"] / 2.0, x + p["lobe_w"] / 2.0

    def role(face: cq.Face, patch: dict) -> tuple[str, str]:
        if patch["support_type"] == "cylinder":
            r = _radius(face)
            for name, radius in (("core", 3.0), ("journals", p["journal_d"] / 2.0), ("lobe_bases", p["lobe_base_r"])):
                if abs(r - radius) < 1e-6:
                    return name, "shaft"
            raise ValueError(f"unexpected cylinder r={r}")
        if patch["support_type"] != "plane":
            return "lobe_noses", "surface"
        x = patch["point_mm"][0]
        for name, level in levels.items():
            if abs(x - level) < 1e-6:
                return name, "plane_face"
        raise ValueError(f"unexpected plane x={x}")

    return _collect(shape, role)
