# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0010 Timing belt, GT2 6 mm, 240 mm closed loop: a bought part, modelled as an envelope for clearance only.

part_local frame: the loop lies in the YZ plane, the crank pulley centre at the origin and the cam pulley centre 90 mm
along +Z (crank axis to cam axis along the bank axis: deck 76 + cam 14). Open-belt length for C = 90 mm, pitch radii
6.37 and 12.73 mm: 2C + pi(r1 + r2) + (r2 - r1)^2 / C = 240.4 mm, so the 120-tooth stock loop fits with a tensioner taking up the rest.
"""

import cadquery as cq

KERNEL = "cadquery"
PARAMS = {
    "pitch_mm": 2.0,
    "teeth": 120,
    "length_mm": 240.0,
    "width": 6.0,
    "thickness": 1.38,  # GT2 overall belt thickness
    "centre_mm": 90.0,
    "r_crank": 6.366,
    "r_cam": 12.732,
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}

    def hull(off: float) -> cq.Workplane:
        a = cq.Workplane("YZ").workplane(offset=-p["width"] / 2.0).circle(p["r_crank"] + off).extrude(p["width"])
        b = cq.Workplane("YZ").workplane(offset=-p["width"] / 2.0).center(0, p["centre_mm"]).circle(p["r_cam"] + off).extrude(p["width"])
        return a.union(b).union(cq.Workplane("YZ").workplane(offset=-p["width"] / 2.0).polyline([(-p["r_crank"] - off, 0), (p["r_crank"] + off, 0), (p["r_cam"] + off, p["centre_mm"]), (-p["r_cam"] - off, p["centre_mm"])]).close().extrude(p["width"]))

    return hull(p["thickness"]).cut(hull(0.0)).val()
