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
from cadmodel.safe import safe_fuse

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
    "nose_d": 10.0,
    "nose_len": 14.0,
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
             _cyl(p["journal_d"] / 2.0, -half + p["seat_len"], half - p["seat_len"])]
    for k, alpha in enumerate(p["pin_angles_deg"]):
        x = (k - 1) * p["pitch"]
        py, pz = p["throw_r"] * math.cos(math.radians(alpha)), p["throw_r"] * math.sin(math.radians(alpha))
        reach = p["pin_len"] / 2.0 + p["web_t"]
        parts.append(_cyl(p["pin_d"] / 2.0, x - reach, x + reach, py, pz))
        parts.append(_web(p, x - reach, x - reach + p["web_t"], alpha))
        parts.append(_web(p, x + reach - p["web_t"], x + reach, alpha))
    return safe_fuse(*parts).val()
