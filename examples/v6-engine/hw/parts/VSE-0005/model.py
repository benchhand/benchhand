# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0005 Crankshaft: CadQuery 2.8, assembly_zero frame, one occurrence.

Zero pose: the crank axis is the X axis through the origin. Three throws at x = -pitch, 0, +pitch; the crank-pin
angle alpha is measured in the YZ plane from +Y toward +Z, so the pin sits at (x, R cos a, R sin a). Each pin
carries two rods, one per bank. Main journals sit in the block's two 14 mm seats; the nose beyond x = -case_len / 2
takes a crank handle or a motor coupling.
"""

import math

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "pitch": 36.0,
    "throw_r": 14.0,
    "pin_d": 12.0,
    "pin_len": 20.0,
    "web_t": 2.0,
    "web_r": 8.0,
    "journal_d": 13.97,  # 14 f7 is 13.984/13.966; the mid-limit rounded to 13.97
    "seat_len": 12.0,
    "case_len": 120.0,
    "link_d": 10.0,  # link shaft between throws; the rod rings pass outside it
    "nose_d": 10.0,
    "nose_len": 14.0,
    "coupling_bore_d": 4.0,  # axial hole in the nose for a hand crank pin or a motor coupling (REQ-006)
    "coupling_bore_len": 12.0,
    "pin_angles_deg": [60.0, 180.0, 300.0],
}


def _cyl(r: float, x0: float, x1: float, y: float = 0.0, z: float = 0.0) -> cq.Workplane:
    return cq.Workplane(obj=cq.Solid.makeCylinder(r, x1 - x0, cq.Vector(x0, y, z), cq.Vector(1, 0, 0)))


def _web(p: dict, x0: float, x1: float, alpha: float) -> cq.Workplane:
    py, pz = p["throw_r"] * math.cos(math.radians(alpha)), p["throw_r"] * math.sin(math.radians(alpha))
    arm = cq.Workplane("XY").box(x1 - x0, 2 * p["web_r"], p["throw_r"], centered=(False, True, False)).translate((x0, 0, 0))
    arm = arm.rotate((0, 0, 0), (1, 0, 0), alpha - 90.0)  # the box points along +Z; turn it to the pin angle
    return safe_fuse(_cyl(p["web_r"], x0, x1), _cyl(p["web_r"], x0, x1, py, pz), arm)


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    half = p["case_len"] / 2.0
    parts = [_cyl(p["nose_d"] / 2.0, -half - p["nose_len"], -half + 1.0),
             _cyl(p["journal_d"] / 2.0, -half, -half + p["seat_len"] + 1.0),
             _cyl(p["journal_d"] / 2.0, half - p["seat_len"] - 1.0, half),
             ]
    reach = p["pin_len"] / 2.0 + p["web_t"]
    for k in (0, 1):  # link shafts between neighbouring throws, inside the rod-free gaps
        x_lo = (k - 1) * p["pitch"] + reach - p["web_t"]
        parts.append(_cyl(p["link_d"] / 2.0, x_lo, x_lo + p["pitch"] - 2.0 * reach + 2.0 * p["web_t"]))
    for k, alpha in enumerate(p["pin_angles_deg"]):
        x = (k - 1) * p["pitch"]
        py, pz = p["throw_r"] * math.cos(math.radians(alpha)), p["throw_r"] * math.sin(math.radians(alpha))
        reach = p["pin_len"] / 2.0 + p["web_t"]
        parts.append(_cyl(p["pin_d"] / 2.0, x - reach, x + reach, py, pz))
        parts.append(_web(p, x - reach, x - reach + p["web_t"], alpha))
        parts.append(_web(p, x + reach - p["web_t"], x + reach, alpha))
    shaft = safe_fuse(*parts)
    coupling = _cyl(p["coupling_bore_d"] / 2.0, -half - p["nose_len"] - 1.0, -half - p["nose_len"] + p["coupling_bore_len"])
    return safe_cut(shaft, coupling).val()


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


def _cylinder_role(face: cq.Face, p: dict, pins: dict) -> tuple[str, str]:
    """An on-axis cylinder by its radius (nose, journal, coupling bore, web hub), else a crank pin or pin web end."""
    surf = BRepAdaptor_Surface(face.wrapped).Cylinder()
    r, loc = surf.Radius(), surf.Location()
    cy, cz = loc.Y(), loc.Z()
    if abs(cy) < 1e-6 and abs(cz) < 1e-6:
        on_axis = (("nose_and_links", p["nose_d"] / 2.0, "shaft"), ("main_journal", p["journal_d"] / 2.0, "shaft"),
                   ("coupling_bore", p["coupling_bore_d"] / 2.0, "bore"), ("web_hubs", p["web_r"], "shaft"))
        for name, radius, kind in on_axis:
            if abs(r - radius) < 1e-6:
                return name, kind
    for k, (py, pz) in pins.items():
        if abs(cy - py) < 1e-6 and abs(cz - pz) < 1e-6:
            return (f"crank_pin_{k}" if abs(r - p["pin_d"] / 2.0) < 1e-6 else f"pin_web_ends_{k}"), "shaft"
    raise ValueError(f"unexpected cylinder r={r} at {cy}, {cz}")


def _plane_role(x: float, n: list, p: dict, half: float) -> tuple[str, str]:
    """An arm flank by throw and side, the nose end, the coupling floor, or an x-normal plane by its place."""
    if abs(n[0]) < 0.99:
        throw = round(x / p["pitch"]) + 2
        side = "a" if (n[2] > 1e-6 or (abs(n[2]) < 1e-6 and n[1] > 0)) else "b"
        return f"arm_flank_{throw}_{side}", ("plane_face" if abs(n[1]) < 1e-6 or abs(n[2]) < 1e-6 else "surface")
    if abs(x + half + p["nose_len"]) < 1e-6:
        return "nose_end", "plane_face"
    if abs(x + half - p["coupling_bore_len"] + p["nose_len"]) < 1e-6:
        return "coupling_floor", "plane_face"
    return f"face_x_{'m' if x < 0 else 'p'}{abs(x):g}".replace(".", "_"), "plane_face"


def drawing_features(params: dict, shape: cq.Shape) -> dict:
    """Nose, link shafts, main journals, crank pins, web hubs and pin ends, arm flanks and every x-normal plane by place."""
    p = {**PARAMS, **params}
    half = p["case_len"] / 2.0
    pins = {k: (p["throw_r"] * math.cos(math.radians(a)), p["throw_r"] * math.sin(math.radians(a))) for k, a in enumerate(p["pin_angles_deg"], 1)}

    def role(face: cq.Face, patch: dict) -> tuple[str, str]:
        if patch["support_type"] == "cylinder":
            return _cylinder_role(face, p, pins)
        return _plane_role(patch["point_mm"][0], patch["normal"], p, half)

    return _collect(shape, role)
