# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""V6E-0001 Crankcase and V block: CadQuery 2.8, 60 degree V, three cylinders per bank.

Zero pose, assembly coordinates: the crank axis is the Y axis through the origin; the V opens toward +Z. The bank
axes lie in the XZ plane, tilted 30 degrees either side of +Z. The left bank sits 6 mm toward +Y of the
right bank, so a connecting rod of each bank can share one crank pin.
"""

import math

import cadquery as cq
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "half_v_deg": 30.0,
    "pitch": 26.0,  # distance between crank throws along Y
    "bank_offset": 6.0,  # the left bank sits +offset, the right bank -offset along Y
    "bore_d": 24.0,
    "deck_h": 70.0,  # bank axis distance from the crank axis to the deck face
    "deck_w": 40.0,
    "case_x": 64.0,
    "case_z": 44.0,
    "length": 96.0,
    "main_bore_d": 14.0,  # crank main bearing seat, 14 H7 in the web walls
    "crank_clearance_d": 46.0,
}


def _bank(p: dict, side: int) -> cq.Workplane:
    """The deck block of one bank, in bank coordinates (axis along +Z), before it is tilted."""
    deck = cq.Workplane("XY").box(p["deck_w"], p["length"], p["deck_h"], centered=(True, True, False))
    return deck.translate((0, side * p["bank_offset"] * 0, 0))


def _tilt(shape: cq.Workplane, angle: float) -> cq.Workplane:
    return shape.rotate((0, 0, 0), (0, 1, 0), angle)


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    half = p["half_v_deg"]
    case = cq.Workplane("XY").box(p["case_x"], p["length"], p["case_z"], centered=(True, True, True))
    body = case
    for side, angle in ((1, half), (-1, -half)):
        body = safe_fuse(body, _tilt(_bank(p, side), angle))
    window = cq.Workplane("XZ").circle(p["crank_clearance_d"] / 2.0).extrude(p["length"] - 24.0, both=True)
    body = safe_cut(body, window.translate((0, 0, 0)))
    for side, angle in ((1, half), (-1, -half)):
        for i in range(3):
            y = (i - 1) * p["pitch"] + side * p["bank_offset"]
            bore = cq.Workplane("XY").circle(p["bore_d"] / 2.0).extrude(p["deck_h"] + 5.0).translate((0, y, 0))
            body = safe_cut(body, _tilt(bore, angle))
    for y in (-p["length"] / 2.0, p["length"] / 2.0 - 12.0):
        seat = cq.Workplane("XZ").workplane(offset=-y - 12.0).circle(p["main_bore_d"] / 2.0).extrude(12.0)
        body = safe_cut(body, seat)
    return body.val()
