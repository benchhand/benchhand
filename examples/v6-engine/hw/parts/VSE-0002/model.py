# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0002 Piston: CadQuery 2.8, part_local frame, placed in six occurrences.

Local frame: the wrist pin axis is the X axis through the origin; the cylinder axis is +Z with the crown at +z.
A slot through the skirt along X leaves room for the rod's small end.
"""

import cadquery as cq
from cadmodel.safe import safe_cut

KERNEL = "cadquery"
PARAMS = {
    "d": 23.6,  # 0.4 mm diametral running clearance in the 24 mm bore
    "crown_h": 10.0,  # crown face above the pin axis
    "skirt_h": 12.0,  # skirt below the pin axis
    "pin_bore_d": 6.012,  # 6 H7 upper limit; the wrist pin takes 6 g6
    "slot_w": 10.0,  # clearance for the 8 mm rod small end
    "slot_top": 4.0,  # slot height above the pin axis
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    h = p["crown_h"] + p["skirt_h"]
    body = cq.Workplane("XY").workplane(offset=-p["skirt_h"]).circle(p["d"] / 2.0).extrude(h)
    slot = cq.Workplane("XY").box(p["d"] + 2.0, p["slot_w"], p["slot_top"] + p["skirt_h"] + 1.0, centered=(True, True, False))
    body = safe_cut(body, slot.translate((0, 0, -p["skirt_h"] - 1.0)))
    bore = cq.Workplane("XZ").circle(p["pin_bore_d"] / 2.0).extrude(p["d"], both=True)
    return safe_cut(body, bore).rotate((0, 0, 0), (0, 0, 1), 90).val()  # pin axis X, rod swing plane YZ
