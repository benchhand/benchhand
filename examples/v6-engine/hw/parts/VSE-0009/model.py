# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0009 Cam pulley: CadQuery 2.8, part_local frame, one occurrence per camshaft stub.

The axis runs along X through the origin. A 40-tooth GT2 pulley (2 mm pitch, pitch diameter 25.46 mm) with flanges,
bored 6 H7 for the 6 mm camshaft stub. Twice the 20 teeth of the crank pulley, so the cam turns once per two crank turns.
"""

import math

import cadquery as cq
from cadmodel.safe import safe_cut, safe_fuse

KERNEL = "cadquery"
PARAMS = {
    "teeth": 40,
    "pitch_mm": 2.0,
    "width": 8.0,
    "flange_t": 1.0,
    "flange_extra_r": 1.5,
    "tooth_depth": 0.75,  # GT2 tooth height
    "bore_d": 6.006,  # 6 H7 mid-limit for the 6 mm camshaft stub
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    pitch_r = p["teeth"] * p["pitch_mm"] / math.pi / 2.0
    root_r = pitch_r - 0.254 - p["tooth_depth"]  # GT2 pitch line sits 0.254 mm outside the tooth tips
    hub_w = p["width"] - 2.0 * p["flange_t"]
    x0 = -p["width"] / 2.0
    body = cq.Workplane("YZ").workplane(offset=x0 + p["flange_t"]).circle(pitch_r - 0.254).extrude(hub_w)
    for i in range(p["teeth"]):  # one groove per tooth
        a = 2.0 * math.pi * i / p["teeth"]
        r = root_r + p["tooth_depth"] / 2.0 + 0.4
        groove = cq.Workplane("YZ").workplane(offset=x0 + p["flange_t"]).center(r * math.cos(a), r * math.sin(a)).circle(0.55).extrude(hub_w)
        body = safe_cut(body, groove)
    for off in (x0, p["width"] / 2.0 - p["flange_t"]):
        body = safe_fuse(body, cq.Workplane("YZ").workplane(offset=off).circle(pitch_r + p["flange_extra_r"]).extrude(p["flange_t"]))
    bore = cq.Workplane("YZ").workplane(offset=x0 - 1.0).circle(p["bore_d"] / 2.0).extrude(p["width"] + 2.0)
    return safe_cut(body, bore).val()
