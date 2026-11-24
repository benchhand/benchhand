# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0012 Smooth idler pulley, 10 mm OD, 3 mm bore, flanged: a bought part, modelled as an envelope.

part_local frame, axis along X through the origin. Two occurrences run on the back of the belt (DR-005): the fixed
idler at (0, 70) and the tensioner roller at z = 30, whose position sets the belt tension.
"""

import cadquery as cq
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "od": 10.0,
    "width": 8.0,
    "flange_d": 13.0,
    "flange_t": 1.0,
    "bore_d": 3.0,
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    x0 = -p["width"] / 2.0
    body = cq.Workplane("YZ").workplane(offset=x0).circle(p["od"] / 2.0).extrude(p["width"])
    for off in (x0, p["width"] / 2.0 - p["flange_t"]):
        body = safe_fuse(body, cq.Workplane("YZ").workplane(offset=off).circle(p["flange_d"] / 2.0).extrude(p["flange_t"]))
    bore = cq.Workplane("YZ").workplane(offset=x0 - 1.0).circle(p["bore_d"] / 2.0).extrude(p["width"] + 2.0)
    return safe_cut(body, bore).val()
