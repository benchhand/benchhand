# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0006 Cylinder head: CadQuery 2.8, part_local frame, placed twice (one per bank). First cut.

The frame origin is the crank axis point of the bank and +Z is the bank axis. The deck face sits at z = deck_h.
The cam axis runs along X, cam_h above the deck face, through an 8 mm journal bore.
"""

import cadquery as cq
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
    "journal_bore_d": 8.04,  # 8 H7 upper limit; the camshaft journal is 8 f7
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
    return body.val()
