# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["cadquery==2.8.0", "cadquery-ocp==7.9.3.1.1", "vtk==9.6.2", "pyyaml>=6.0"]
# ///
"""Measure the installed GT2 belt route of a built V6 engine version; prints JSON.

Usage: uv run --script v6_belt_route.py hw/versions/vN
The wrap centres are the occurrence placements in the version's record snapshot. Each wrap's running radius is
measured on the built STEP of its part: the middle of the three cylinders coaxial with the part axis (bore, running
surface, flange). GT2 puts the pitch line 0.254 mm outside a pulley's tooth tips, and 0.376 mm inside the belt back
(0.63 mm land less 0.254 mm), so a back-side roller adds 0.376 mm to its outer radius.
`contacts` gives, per back-side roller, the 3D distance from the placed belt to the roller's running cylinder and to
the whole roller. A roller shifted along its axis can touch the belt with a flange alone: then only the whole-part
distance is 0.
"""

import json
import math
import sys
from pathlib import Path

import cadquery as cq
import yaml

PULLEY_PLD_MM = 0.254
BACK_PITCH_MM = 0.376
# Loop order and side: +1 wraps the tooth side, -1 runs on the belt back.
ROUTE = (("crank-pulley", "VSE-0008", 1), ("cam-pulley-a", "VSE-0009", 1), ("idler", "VSE-0012", -1),
         ("cam-pulley-b", "VSE-0009", 1), ("tensioner", "VSE-0012", -1))


def running_radius(step: Path) -> float:
    radii = set()
    for face in cq.importers.importStep(str(step)).faces("%CYLINDER").vals():
        surface = face._geomAdaptor().Cylinder()
        axis, location = surface.Axis().Direction(), surface.Location()
        if abs(abs(axis.X()) - 1.0) < 1e-9 and math.hypot(location.Y(), location.Z()) < 1e-6:
            radii.add(round(surface.Radius(), 6))
    if len(radii) != 3:
        raise SystemExit(f"{step.name}: expected bore, running surface and flange on the axis, found radii {sorted(radii)}")
    return sorted(radii)[1]


def tangent(c1, r1, c2, r2):
    dy, dz = c2[0] - c1[0], c2[1] - c1[1]
    length = math.hypot(dy, dz)
    ey, ez = dy / length, dz / length
    a = (r2 - r1) / length
    b = math.sqrt(1.0 - a * a)
    ny, nz = a * ey - b * ez, a * ez + b * ey
    return (c1[0] - r1 * ny, c1[1] - r1 * nz), (c2[0] - r2 * ny, c2[1] - r2 * nz)


def loop_length(wraps):
    runs = [tangent(*wraps[i], *wraps[(i + 1) % len(wraps)]) for i in range(len(wraps))]
    total = sum(math.dist(*run) for run in runs)
    for i, (c, r) in enumerate(wraps):
        a0 = math.atan2(runs[i - 1][1][1] - c[1], runs[i - 1][1][0] - c[0])
        a1 = math.atan2(runs[i][0][1] - c[1], runs[i][0][0] - c[0])
        total += abs(r) * ((a1 - a0) % math.tau if r > 0 else (a0 - a1) % math.tau)
    return total


def main() -> None:
    version = Path(sys.argv[1])
    occurrences = {o["id"]: o for o in yaml.safe_load((version / "record" / "assembly.yaml").read_text(encoding="utf-8"))["occurrences"]}
    wraps, report = [], {}
    for oid, pn, side in ROUTE:
        placement = occurrences[oid]["placement"]
        if placement.get("rotation", {}).get("axis", [1, 0, 0]) != [1, 0, 0]:
            raise SystemExit(f"{oid}: the route assumes every wrap turns about X")
        _, y, z = placement["translation_mm"]
        running = running_radius(version / "parts" / f"{pn}.step")
        pitch = running + PULLEY_PLD_MM if side > 0 else running + BACK_PITCH_MM
        wraps.append(((y, z), side * pitch))
        report[oid] = {"centre_yz_mm": [y, z], "running_radius_mm": running, "pitch_radius_mm": round(pitch, 6)}
    belt = placed(version / "parts" / "VSE-0010.step", occurrences["belt"]["placement"])
    contacts = {}
    for oid, pn, side in ROUTE:
        if side > 0:
            continue
        roller = placed(version / "parts" / f"{pn}.step", occurrences[oid]["placement"])
        running = [f for f in roller.Faces() if f.geomType() == "CYLINDER"
                   and abs(f._geomAdaptor().Cylinder().Radius() - report[oid]["running_radius_mm"]) < 1e-6]
        contacts[oid] = {"running_surface_mm": round(min(belt.distance(f) for f in running), 6),
                         "whole_part_mm": round(belt.distance(roller), 6)}
    print(json.dumps({"pitch_length_mm": round(loop_length(wraps), 4), "wraps": report, "contacts": contacts}))


def placed(step: Path, placement: dict) -> cq.Shape:
    """The part's STEP shape at its occurrence placement, so distances keep the axial (X) coordinate."""
    rotation = placement.get("rotation", {})
    location = cq.Location(cq.Vector(*placement["translation_mm"]), cq.Vector(*rotation.get("axis", [1, 0, 0])),
                           rotation.get("angle_deg", 0.0))
    return cq.importers.importStep(str(step)).val().moved(location)


main()
