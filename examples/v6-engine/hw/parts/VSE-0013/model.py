# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0013 Belt idler bracket: CadQuery 2.8, part_local frame, one occurrence on the crankcase end face (DR-005).

The v9 connection check found the idler 8.88 mm and the tensioner 4.75 mm off the crankcase, so nothing held them.
Local origin: the crankcase end face (x = 0), crank axis along X. The plate lies at local x -2..0 and spans y -13..13, z 10..76.
Two 3 g6 stub axles (2.995 mm) run to local x -12.75: one at (y 0, z 70) for the idler, one at (y -10.7, z 30) for the tensioner roller.
The 3.0 mm roller bore rides on them with 0.005 mm radial clearance; the v10 connection check flagged 0.03 mm as floating.
"""

import cadquery as cq
from cadmodel.safe import safe_fuse
from OCP.BRep import BRep_Tool
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf

KERNEL = "cadquery"
PARAMS = {
    "plate_t": 2.0,
    "plate_y": 26.0,
    "plate_z0": 10.0,
    "plate_z1": 76.0,
    "axle_d": 2.995,  # 3 g6 mid-limit (2.992..2.998)
    "axle_len": 12.75,
    "idler_yz": (0.0, 70.0),
    "tensioner_yz": (-10.7, 30.0),
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    plate = cq.Workplane("XY").box(p["plate_t"], p["plate_y"], p["plate_z1"] - p["plate_z0"], centered=(False, True, False)).translate((-p["plate_t"], 0, p["plate_z0"]))
    body = plate
    for y, z in (p["idler_yz"], p["tensioner_yz"]):
        axle = cq.Solid.makeCylinder(p["axle_d"] / 2.0, p["axle_len"] - p["plate_t"] + 0.5, cq.Vector(-p["axle_len"], y, z), cq.Vector(1, 0, 0))
        body = safe_fuse(body, cq.Workplane(obj=axle))
    return body.val()


SUPPORT = {"PLANE": "plane", "CYLINDER": "cylinder"}


def _witness(face: cq.Face) -> dict:
    """A final-surface witness: the centre of the face's largest mesh triangle, projected onto the face."""
    vertices, triangles = face.tessellate(0.01, 0.2)
    a, b, c = max(((vertices[i], vertices[j], vertices[k]) for i, j, k in triangles),
                  key=lambda t: (t[1] - t[0]).cross(t[2] - t[0]).Length)
    centre = (a + b + c) * (1.0 / 3.0)
    near = GeomAPI_ProjectPointOnSurf(centre.toPnt(), BRep_Tool.Surface_s(face.wrapped)).NearestPoint()
    point = cq.Vector(near.X(), near.Y(), near.Z())
    return {"support_type": SUPPORT.get(face.geomType(), "other"), "point_mm": list(point.toTuple()),
            "normal": list(face.normalAt(point).normalized().toTuple())}


def drawing_features(params: dict, shape: cq.Shape) -> dict:
    """Every final face by its role: the mount face, the plate edges, and each axle with its end face."""
    p = {**PARAMS, **params}
    features: dict[str, tuple[str, list]] = {}

    def add(name: str, kind: str, patch: dict) -> None:
        features.setdefault(name, (kind, []))[1].append(patch)

    for face in shape.Faces():
        patch = _witness(face)
        x, y, z = patch["point_mm"]
        n = patch["normal"]
        idler = abs(y - p["idler_yz"][0]) < 2.0
        if patch["support_type"] == "cylinder":
            add("axle_idler" if idler else "axle_tensioner", "shaft", patch)
        elif abs(n[0]) > 0.5 and x > -1e-6:
            add("mount_face", "plane_face", patch)
        elif abs(n[0]) > 0.5 and abs(x + p["plate_t"]) < 1e-6:
            add("axle_face", "plane_face", patch)
        elif abs(n[0]) > 0.5:
            add("axle_end_idler" if idler else "axle_end_tensioner", "plane_face", patch)
        elif abs(n[1]) > 0.5:
            add("side_face_low" if y < 0 else "side_face_high", "plane_face", patch)
        else:
            add("top_edge" if z > (p["plate_z0"] + p["plate_z1"]) / 2.0 else "bottom_edge", "plane_face", patch)
    return {name: {"kind": kind, "frame": "assembly_zero", "patches": patches} for name, (kind, patches) in features.items()}
