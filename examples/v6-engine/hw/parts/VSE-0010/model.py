# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0010 Timing belt, GT2 6 mm, 420 mm closed loop (210 teeth): a bought part, modelled as a clearance envelope.

DR-005: one serpentine belt in the plane x = -68.75. part_local frame: the loop lies in the YZ plane with the crank
pulley at the origin. It wraps the crank pulley, both cam pulleys at (+/-45, 77.94) and an idler at (0, 70) on its
back side. The back-side tensioner at z = 30 travels y -30..-6, giving 411.2..424.3 mm; 420 mm sits at y = -10.7.
The envelope is the convex hull of the three pulleys; the idler and tensioner dips are left out.
"""

import cadquery as cq

KERNEL = "cadquery"
PARAMS = {
    "pitch_mm": 2.0,
    "teeth": 210,
    "length_mm": 420.0,
    "width": 6.0,
    "thickness": 1.38,  # GT2 overall belt thickness
    "cam_y": 45.0,
    "cam_z": 77.942,
    "r_crank": 6.366,
    "r_cam": 12.732,
}


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}

    def hull(off: float) -> cq.Workplane:
        wp = cq.Workplane("YZ").workplane(offset=-p["width"] / 2.0)
        crank = wp.circle(p["r_crank"] + off).extrude(p["width"])
        cams = [wp.center(sy * p["cam_y"], p["cam_z"]).circle(p["r_cam"] + off).extrude(p["width"]) for sy in (-1, 1)]
        y, z, rc, rk = p["cam_y"], p["cam_z"], p["r_cam"] + off, p["r_crank"] + off
        core = wp.polyline([(-rk, 0), (rk, 0), (y + rc, z), (y, z + rc), (-y, z + rc), (-y - rc, z)]).close().extrude(p["width"])
        return crank.union(cams[0]).union(cams[1]).union(core)

    return hull(p["thickness"]).cut(hull(0.0)).val()
