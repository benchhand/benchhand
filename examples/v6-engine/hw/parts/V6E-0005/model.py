# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""V6E-0005 Crankshaft: CadQuery 2.8, assembly_zero frame, one occurrence.

Zero pose: the crank axis is the Y axis through the origin. Three throws at y = -pitch, 0, +pitch with crank-pin
angles 0, 120 and 240 degrees, measured in the XZ plane from +X toward +Z. Each pin carries two rods, one per bank.
Main journals sit in the block's two 14 mm seats; the nose beyond y = -48 takes a crank handle or a motor coupling.
"""

import math

import cadquery as cq
from cadmodel.safe import safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "pitch": 26.0,
    "throw_r": 14.0,
    "pin_d": 12.0,
    "pin_len": 20.0,
    "web_t": 2.0,
    "web_r": 8.0,
    "journal_d": 13.97,  # 14 f7 upper deviation -0.014... mid-limit of 14 f7 (13.984/13.966) rounded to 13.97
    "seat_len": 12.0,
    "case_len": 96.0,
    "nose_d": 10.0,
    "nose_len": 14.0,
    "phases_deg": [0.0, 120.0, 240.0],
}


def _cyl(r: float, y0: float, y1: float, x: float = 0.0, z: float = 0.0) -> cq.Workplane:
    return cq.Workplane(obj=cq.Solid.makeCylinder(r, y1 - y0, cq.Vector(x, y0, z), cq.Vector(0, 1, 0)))


def _web(p: dict, y0: float, y1: float, phase: float) -> cq.Workplane:
    px, pz = p["throw_r"] * math.cos(math.radians(phase)), p["throw_r"] * math.sin(math.radians(phase))
    web = safe_fuse(_cyl(p["web_r"], y0, y1), _cyl(p["web_r"], y0, y1, px, pz))
    arm = cq.Workplane("XY").box(2 * p["web_r"], y1 - y0, p["throw_r"], centered=(True, False, False)).translate((0, y0, 0))
    arm = arm.rotate((0, 0, 0), (0, 1, 0), -(phase - 90.0))
    return safe_fuse(web, arm)


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    half = p["case_len"] / 2.0
    parts = [_cyl(p["nose_d"] / 2.0, -half - p["nose_len"], -half),
             _cyl(p["journal_d"] / 2.0, -half, -half + p["seat_len"]),
             _cyl(p["journal_d"] / 2.0, half - p["seat_len"], half)]
    shaft = _cyl(p["journal_d"] / 2.0, -half + p["seat_len"] - 0.5, half - p["seat_len"] + 0.5)
    parts.append(shaft)
    for i, phase in enumerate(p["phases_deg"]):
        y = (i - 1) * p["pitch"]
        px, pz = p["throw_r"] * math.cos(math.radians(phase)), p["throw_r"] * math.sin(math.radians(phase))
        parts.append(_cyl(p["pin_d"] / 2.0, y - p["pin_len"] / 2.0 - p["web_t"], y + p["pin_len"] / 2.0 + p["web_t"], px, pz))
        for side in (-1, 1):
            a = y + side * p["pin_len"] / 2.0
            parts.append(_web(p, min(a, a + side * p["web_t"]), max(a, a + side * p["web_t"]), phase))
    return safe_fuse(*parts).val()
