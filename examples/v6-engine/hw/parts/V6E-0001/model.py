# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""V6E-0001 Crankcase and V block: CadQuery 2.8, 60 degree V, three cylinders per bank.

Zero pose, assembly coordinates: the crank axis is the X axis through the origin; the V opens toward +Z. Each bank
axis n = (0, -sin b, cos b) is the +Z axis turned about +X by the bank angle b = -30 degrees (right bank, odd
cylinders) or +30 degrees (left bank, even). Crank throw k sits at x = (k - 1) * pitch; the right bank's cylinder is
5.25 mm toward -X of its throw and the left bank's 5.25 mm toward +X, so the two rods of a throw sit side by side.
"""

import math

import cadquery as cq
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
    "window_d": 62.0,  # crank and rod clearance, along X
    "window_len": 96.0,
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
