# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0004 Connecting rod: CadQuery 2.8, part_local frame, placed in six occurrences.

Local frame: the small end (wrist pin) axis is the Y axis through the origin; the big end (crank pin) axis is
parallel to it at z = -rod_length. The rod is a flat web 8 mm thick along Y with an I-section lightening on both faces.
"""

import cadquery as cq
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "rod_length": 48.0,  # centre to centre
    "thickness": 8.0,
    "small_r": 6.0,
    "big_r": 10.0,
    "small_bore_d": 6.1,  # 0.1 mm over the 6 g6 pin: the rod turns on the pin
    "big_bore_d": 12.2,  # 12 mm crank pin, 0.2 mm running clearance for FDM
    "web_w": 7.0,
    "pocket_depth": 1.5,
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    t, length = p["thickness"], p["rod_length"]
    small = cq.Workplane("XZ").circle(p["small_r"]).extrude(t / 2.0, both=True)
    big = cq.Workplane("XZ").center(0, -length).circle(p["big_r"]).extrude(t / 2.0, both=True)
    web = cq.Workplane("XY").box(p["web_w"], t, length, centered=(True, True, False)).translate((0, 0, -length))
    body = safe_fuse(small, big, web)
    for sign in (1, -1):
        pocket = cq.Workplane("XY").box(3.0, p["pocket_depth"], length - p["small_r"] - p["big_r"] - 6.0, centered=(True, False, False))
        pocket = pocket.translate((0, t / 2.0 - p["pocket_depth"] if sign > 0 else -t / 2.0, -length + p["big_r"] + 3.0))
        body = safe_cut(body, pocket)
    body = safe_cut(body, cq.Workplane("XZ").circle(p["small_bore_d"] / 2.0).extrude(t, both=True))
    body = safe_cut(body, cq.Workplane("XZ").center(0, -length).circle(p["big_bore_d"] / 2.0).extrude(t, both=True))
    return body.rotate((0, 0, 0), (0, 0, 1), 90).val()  # wrist axis X, rod swing plane YZ
