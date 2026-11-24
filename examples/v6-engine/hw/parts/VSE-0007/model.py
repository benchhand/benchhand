# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0007 Camshaft: CadQuery 2.8, part_local frame, placed twice (one per head). First cut.

The axis runs along X through the origin; the part is 156 mm long. Two 8 f7 journals ride in the head's 8 H7 bore. Three lobes sit under the three
cylinders (36 mm pitch). The 6 mm stub at the -X end (local x -78..-70) carries the cam pulley, so both pulleys sit on the crank nose side.
"""

import cadquery as cq

KERNEL = "cadquery"
PARAMS = {
    "length": 156.0,
    "journal_d": 7.987,  # 8 f7 upper deviation -0.013; the head bore is 8 H7
    "journal_x": (-50.0, -18.0, 18.0, 50.0),
    "journal_w": 8.0,
    "lobe_x": (-36.0, 0.0, 36.0),
    "lobe_w": 6.0,
    "lobe_base_r": 5.5,
    "lobe_lift": 1.5,  # the nose reaches 7 mm from the axis
    "stub_d": 6.0,
    "stub_len": 8.0,  # the pulley seat at the -X end
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    half = p["length"] / 2.0
    shaft_len = p["length"] - p["stub_len"]
    shaft = cq.Workplane("YZ").workplane(offset=-half + p["stub_len"]).circle(3.0).extrude(shaft_len)  # 6 mm core between the journals and lobes
    stub = cq.Workplane("YZ").workplane(offset=-half).circle(p["stub_d"] / 2.0).extrude(p["stub_len"])
    body = shaft.union(stub)
    for x in p["journal_x"]:
        body = body.union(cq.Workplane("YZ").workplane(offset=x - p["journal_w"] / 2.0).circle(p["journal_d"] / 2.0).extrude(p["journal_w"]))
    for x in p["lobe_x"]:
        lobe = cq.Workplane("YZ").workplane(offset=x - p["lobe_w"] / 2.0).circle(p["lobe_base_r"]).extrude(p["lobe_w"])
        nose = cq.Workplane("YZ").workplane(offset=x - p["lobe_w"] / 2.0).center(0, p["lobe_base_r"]).ellipse(p["lobe_base_r"] * 0.45, p["lobe_lift"] + 0.0).extrude(p["lobe_w"])
        body = body.union(lobe).union(nose)
    return body.val()
