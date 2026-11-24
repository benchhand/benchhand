# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0013 Belt idler bracket: CadQuery 2.8, part_local frame, one occurrence on the crankcase end face (DR-005).

The v9 connection check found the idler 8.88 mm and the tensioner 4.75 mm off the crankcase, so nothing held them.
Local origin: the crankcase end face (x = 0), crank axis along X. The plate lies at local x -2..0 and spans y -13..13, z 10..76.
Two 2.94 mm stub axles run to local x -12.75: one at (y 0, z 70) for the idler, one at (y -10.7, z 30) for the tensioner roller.
The 3.0 mm roller bore rides on them with 0.06 mm diametral clearance.
"""

import cadquery as cq
from cadmodel.safe import safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "plate_t": 2.0,
    "plate_y": 26.0,
    "plate_z0": 10.0,
    "plate_z1": 76.0,
    "axle_d": 2.94,
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
