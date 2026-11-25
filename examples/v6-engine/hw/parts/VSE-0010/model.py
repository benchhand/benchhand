# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""VSE-0010 Timing belt, GT2 6 mm, 360 mm closed loop (180 teeth): a bought part, modelled as a routed envelope.

DR-005: one serpentine belt in the plane x = -68.75. part_local frame: the loop lies in the YZ plane with the crank
pulley at the origin. The tooth side wraps the crank pulley and both cam pulleys at (+/-45, 77.94); the back side runs
over the idler at (0, 70) and the tensioner at (-12.6, 30), both 10 mm rollers. The envelope follows that path: tangent
runs between the five wraps. On a pulley its inner face is the pitch circle, because the teeth sit in the grooves. On
a roller its back face lies on the 5 mm roller radius, so the belt touches both rollers.
The pitch line runs 0.376 mm inside the back (GT2 land 0.63 mm, pitch line 0.254 mm above the tooth root), so the
rollers add 5.376 mm of pitch radius. The tensioner travel y -30..-6 spans 352.4..365.9 mm of pitch length; the 360 mm
loop sits at y = -12.66, and y = -12.6 stretches it by 0.04 mm.
"""

import math

import cadquery as cq

KERNEL = "cadquery"
PARAMS = {
    "pitch_mm": 2.0,
    "teeth": 180,
    "length_mm": 360.0,
    "width": 6.0,
    "thickness": 1.38,  # GT2 overall belt thickness
    "cam_y": 45.0,
    "cam_z": 77.942,
    "r_crank": 6.366,
    "r_cam": 12.732,
    "r_roller": 5.0,
    "idler_yz": (0.0, 70.0),
    "tensioner_yz": (-12.6, 30.0),
}


def wraps(p: dict, off: float) -> list[tuple[tuple[float, float], float]]:
    """The belt path in loop order, as (centre, signed radius) in (y, z): positive wraps the tooth side, negative the
    back side. `off` moves the curve outward from the pulleys, so off = 0 is the inner face."""
    back = p["r_roller"] + p["thickness"] - off
    return [((0.0, 0.0), p["r_crank"] + off), ((p["cam_y"], p["cam_z"]), p["r_cam"] + off), (p["idler_yz"], -back),
            ((-p["cam_y"], p["cam_z"]), p["r_cam"] + off), (p["tensioner_yz"], -back)]


def _tangent(c1, r1, c2, r2):
    """The run leaving circle 1 for circle 2, each on its signed side; returns the two tangent points."""
    dy, dz = c2[0] - c1[0], c2[1] - c1[1]
    length = math.hypot(dy, dz)
    ey, ez = dy / length, dz / length
    a = (r2 - r1) / length
    b = math.sqrt(1.0 - a * a)
    ny, nz = a * ey - b * ez, a * ez + b * ey
    return (c1[0] - r1 * ny, c1[1] - r1 * nz), (c2[0] - r2 * ny, c2[1] - r2 * nz)


def path_length(circles: list[tuple[tuple[float, float], float]]) -> float:
    """Length of the closed path of tangent runs and wraps."""
    runs = [_tangent(*circles[i], *circles[(i + 1) % len(circles)]) for i in range(len(circles))]
    total = sum(math.dist(*run) for run in runs)
    for i, (c, r) in enumerate(circles):
        start, end = runs[i - 1][1], runs[i][0]
        a0 = math.atan2(start[1] - c[1], start[0] - c[0])
        a1 = math.atan2(end[1] - c[1], end[0] - c[0])
        total += abs(r) * ((a1 - a0) % math.tau if r > 0 else (a0 - a1) % math.tau)
    return total


def _wire(circles: list[tuple[tuple[float, float], float]], x: float) -> cq.Wire:
    point = lambda yz: cq.Vector(x, yz[0], yz[1])  # noqa: E731
    runs = [_tangent(*circles[i], *circles[(i + 1) % len(circles)]) for i in range(len(circles))]
    edges = []
    for i, (c, r) in enumerate(circles):
        start, end = runs[i - 1][1], runs[i][0]
        a0 = math.atan2(start[1] - c[1], start[0] - c[0])
        a1 = math.atan2(end[1] - c[1], end[0] - c[0])
        sweep = (a1 - a0) % math.tau if r > 0 else -((a0 - a1) % math.tau)
        mid = a0 + sweep / 2.0
        edges.append(cq.Edge.makeThreePointArc(point(start), point((c[0] + abs(r) * math.cos(mid), c[1] + abs(r) * math.sin(mid))), point(end)))
        edges.append(cq.Edge.makeLine(point(end), point(runs[i][1])))
    return cq.Wire.assembleEdges(edges)


def build(params: dict) -> cq.Shape:
    p = {**PARAMS, **params}
    x0 = -p["width"] / 2.0
    band = cq.Face.makeFromWires(_wire(wraps(p, p["thickness"]), x0), [_wire(wraps(p, 0.0), x0)])
    return cq.Solid.extrudeLinear(band, cq.Vector(p["width"], 0.0, 0.0))


def pitch_length(params: dict) -> float:
    """The pitch length of the path: pitch circles on the pulleys, roller radius plus 0.376 mm on the rollers."""
    p = {**PARAMS, **params}
    pitch = {**p, "thickness": 0.0, "r_roller": p["r_roller"] + 0.376}
    return path_length(wraps(pitch, 0.0))
