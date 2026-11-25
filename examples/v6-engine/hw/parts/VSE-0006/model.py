# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0006 Cylinder head: CadQuery 2.8, part_local frame, placed twice (one per bank). First cut.

The frame origin is the crank axis point of the bank and +Z is the bank axis. The deck face sits at z = deck_h.
The cam axis runs along X, cam_h above the deck face, through an 8 mm journal bore.
"""

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from cadmodel.safe import safe_cut

KERNEL = "cadquery"
PARAMS = {
    "deck_h": 76.0,  # crank axis to deck face along the bank axis
    "head_len": 108.0,
    "head_w": 42.0,
    "head_h": 28.0,
    "cam_h": 14.0,  # cam axis above the deck face
    "cyl_pitch": 36.0,  # cylinder pitch along X within one bank
    "chamber_d": 26.0,  # combustion chamber pocket over the 24 mm bore
    "chamber_h": 3.0,  # pocket depth above the deck face; leaves 7 mm to the cam journal bore
    "journal_bore_d": 8.0075,  # 8 H7 mid-limit (8.000..8.015); the camshaft journal is 8 f7
    "lobe_pocket_r": 7.5,  # cam lobe nose reaches 7.0 mm
    "lobe_pocket_w": 7.0,  # lobe 6 mm wide
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    body = cq.Workplane("XY").box(p["head_len"], p["head_w"], p["head_h"], centered=(True, True, False)).translate((0, 0, p["deck_h"]))
    half = p["head_len"] / 2.0
    bore = cq.Solid.makeCylinder(p["journal_bore_d"] / 2.0, p["head_len"] + 2.0, cq.Vector(-half - 1.0, 0, p["deck_h"] + p["cam_h"]), cq.Vector(1, 0, 0))
    body = safe_cut(body, cq.Workplane(obj=bore))
    # One combustion chamber pocket above each cylinder of the bank (cylinder pitch 36 mm along X, head centred on the middle cylinder).
    for i in (-1, 0, 1):
        pocket = cq.Solid.makeCylinder(p["chamber_d"] / 2.0, p["chamber_h"], cq.Vector(i * p["cyl_pitch"], 0, p["deck_h"]), cq.Vector(0, 0, 1))
        body = safe_cut(body, cq.Workplane(obj=pocket))
    # A lobe pocket around each cam lobe (lobe nose 7 mm from the axis, 0.5 mm running clearance); the 8 H7 bore alone left the lobes buried.
    for i in (-1, 0, 1):
        x = i * p["cyl_pitch"] - p["lobe_pocket_w"] / 2.0
        lobe_pocket = cq.Solid.makeCylinder(p["lobe_pocket_r"], p["lobe_pocket_w"], cq.Vector(x, 0, p["deck_h"] + p["cam_h"]), cq.Vector(1, 0, 0))
        body = safe_cut(body, cq.Workplane(obj=lobe_pocket))
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
    return {name: {"kind": kind, "frame": "part_local", "patches": patches} for name, (kind, patches) in named.items()}


def _cylinder_role(face: cq.Face, p: dict) -> tuple[str, str]:
    """The journal bore, a lobe pocket or a chamber wall, by radius."""
    r = _radius(face)
    for name, radius in (("journal_bore", p["journal_bore_d"] / 2.0), ("lobe_pockets", p["lobe_pocket_r"]), ("chamber_walls", p["chamber_d"] / 2.0)):
        if abs(r - radius) < 1e-6:
            return name, "bore"
    raise ValueError(f"unexpected cylinder r={r}")


def _plane_role(point: list, nz: float, p: dict, flanks: dict) -> tuple[str, str]:
    """A z level (deck, top, chamber floors), a side or end face, or a lobe pocket flank."""
    x, y, z = point
    half = p["head_len"] / 2.0
    levels = (("deck_face", p["deck_h"]), ("top_face", p["deck_h"] + p["head_h"]), ("chamber_floors", p["deck_h"] + p["chamber_h"]))
    if abs(nz) > 0.99:
        for name, level in levels:
            if abs(z - level) < 1e-6:
                return name, "plane_face"
    if abs(abs(y) - p["head_w"] / 2.0) < 1e-6:
        return ("side_face_pos" if y > 0 else "side_face_neg"), "plane_face"
    if abs(abs(x) - half) < 1e-6:
        return ("end_face_pos" if x > 0 else "end_face_neg"), "plane_face"
    for name, level in flanks.items():
        if abs(x - level) < 1e-6:
            return name, "plane_face"
    raise ValueError(f"unexpected plane at {x}, {y}, {z}")


def drawing_features(params: dict, shape: cq.Shape) -> dict:
    """The six outer faces, the journal bore, the chamber walls and floor, and the lobe pocket walls and flanks."""
    p = {**PARAMS, **params}
    flanks = {}
    for i, k in enumerate((-1, 0, 1), 1):
        c = k * p["cyl_pitch"]
        flanks[f"lobe_pocket_{i}_face_a"], flanks[f"lobe_pocket_{i}_face_b"] = c - p["lobe_pocket_w"] / 2.0, c + p["lobe_pocket_w"] / 2.0

    def role(face: cq.Face, patch: dict) -> tuple[str, str]:
        if patch["support_type"] == "cylinder":
            return _cylinder_role(face, p)
        return _plane_role(patch["point_mm"], patch["normal"][2], p, flanks)

    return _collect(shape, role)
