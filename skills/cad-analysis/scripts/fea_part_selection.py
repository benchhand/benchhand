# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""FreeCAD-independent STEP solid and occurrence selection for static analysis."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


_PART_NAME = re.compile(r"^([A-Z]{2,5}-\d{4})(?![0-9])")
# An occurrence reference `@id`; an explicit occurrence's STEP component is named `<PN> <title> [@id]`
# (cad-motion pose.component_name, written by cad-model and by cad-motion's posed and worst-case STEPs).
OCCURRENCE_REF = re.compile(r"@[a-z][a-z0-9_-]*")
_OCCURRENCE_TAG = re.compile(rf" \[({OCCURRENCE_REF.pattern})\]$")


@dataclass(frozen=True)
class SceneObject:
    """One object of the document FreeCAD's Import.insert built from a STEP, described without FreeCAD."""

    names: tuple[str, ...]  # its Label, then its internal Name
    part_feature: bool  # derived from Part::Feature: it holds geometry; App::Part and App::Link containers do not
    volumes: tuple[float, ...]  # the volume of each of its solids, mm^3
    containers: tuple[int, ...] = ()  # indices of its enclosing containers, nearest first


class MultipleSolids(ValueError):
    """The selected part is several solids; the static solver analyses exactly one."""

    def __init__(self, names: Sequence[str], solid_indices: Sequence[int], volumes: Sequence[float]) -> None:
        listed = ", ".join(f"solid_index {index}: {volume:.6g} mm^3" for index, volume in zip(solid_indices, volumes))
        super().__init__(f"part {names[0]!r} has {len(volumes)} solids ({listed}); the static solver analyses exactly "
                         "one and never fuses them: select one with solid_index, select one occurrence, or fix the "
                         "model so that the part is one solid")
        self.details: dict[str, Any] = {"count": len(volumes), "volumes_mm3": list(volumes),
                                        "solid_indices": list(solid_indices)}


def _part_number(names: Sequence[str]) -> str | None:
    return next((match.group(1) for name in names if (match := _PART_NAME.match(name)) is not None), None)


def _owner(objects: Sequence[SceneObject], leaf: int) -> int:
    """The object that names the part a leaf belongs to.

    A leaf without a part number (cad-model's part STEP of a located shape imports as a container named after
    the part around an unnamed "SOLID") takes its nearest container that carries one. Leaves inside a container
    that carries their own part number are the solids of that one part.
    """
    number = _part_number(objects[leaf].names)
    for container in objects[leaf].containers:
        found = _part_number(objects[container].names)
        if found is not None and (number is None or found == number):
            return container
    return leaf


def select_part_solid(objects: Sequence[SceneObject], part: str | None,
                      solid_index: int | None = None) -> tuple[int, int]:
    """The (object index, solid index within that object) of the one solid to analyse.

    Only Part::Feature-derived objects are candidates: a container's shape repeats its children's solids.
    `solid_index` counts the candidates' solids, 1-based, in document order.
    """
    solids = [(index, solid) for index, item in enumerate(objects) if item.part_feature
              for solid in range(len(item.volumes))]
    if solid_index is not None:
        if solid_index < 1 or solid_index > len(solids):
            raise ValueError(f"solid_index {solid_index} is out of range for {len(solids)} STEP solids")
        return solids[solid_index - 1]
    parts: dict[int, list[int]] = {}
    for position, (index, _) in enumerate(solids):
        parts.setdefault(_owner(objects, index), []).append(position)
    owners = list(parts)
    chosen = owners[select_solid_index([objects[owner].names for owner in owners], part)]
    positions = parts[chosen]
    if len(positions) > 1:
        raise MultipleSolids(objects[chosen].names, [position + 1 for position in positions],
                             [objects[solids[p][0]].volumes[solids[p][1]] for p in positions])
    return solids[positions[0]]


def select_solid_index(names_by_solid: Sequence[Sequence[str]], part: str | None, solid_index: int | None = None) -> int:
    """Select one STEP solid by 1-based index or PN-first product/label names."""
    if solid_index is not None:
        if solid_index < 1 or solid_index > len(names_by_solid):
            raise ValueError(f"solid_index {solid_index} is out of range for {len(names_by_solid)} STEP solids")
        return solid_index - 1
    available = sorted({
        match.group(1)
        for names in names_by_solid
        for name in names
        if (match := _PART_NAME.match(name)) is not None
    })
    listed = ", ".join(available) if available else "(none recognized)"

    if part is None:
        if len(names_by_solid) == 1:
            return 0
        if not names_by_solid:
            raise ValueError("STEP contains no solid geometry")
        raise ValueError(f"STEP contains {len(names_by_solid)} solids; part is required. Available PNs: {listed}")

    matches = [
        index
        for index, names in enumerate(names_by_solid)
        if any((match := _PART_NAME.match(name)) is not None and match.group(1) == part for name in names)
    ]
    if not matches:
        raise ValueError(f"part {part!r} matches no STEP solid. Available PNs: {listed}")
    if len(matches) > 1:
        raise ValueError(f"part {part!r} matches {len(matches)} STEP solids; a part placed more than once is "
                         f"selected by occurrence: \"@id\". Available PNs: {listed}")
    return matches[0]


def select_occurrence(labels: Sequence[str], occurrence: str) -> int:
    """Select the one STEP component whose name carries the tag `[@id]` of `occurrence`."""
    tags = [match.group(1) if (match := _OCCURRENCE_TAG.search(label)) else None for label in labels]
    listed = ", ".join(sorted({tag for tag in tags if tag})) or "(none: no component name carries [@id])"
    matches = [index for index, tag in enumerate(tags) if tag == occurrence]
    if not matches:
        raise ValueError(f"occurrence {occurrence!r} matches no STEP component. Available occurrences: {listed}")
    if len(matches) > 1:
        raise ValueError(f"occurrence {occurrence!r} names {len(matches)} STEP components. "
                         f"Available occurrences: {listed}")
    return matches[0]


def selected_face_indices(face_count: int, selector: dict[str, Any], faces: Any = None) -> list[int]:
    """Resolve a selector against a shape's faces, failing when no face matches.

    The host validates selectors with it and the FreeCAD job resolves them with it, against the faces of the
    solid at its placement in the STEP, the frame Gmsh meshes in.
    """
    if "indices" in selector:
        indices = list(selector["indices"])
    else:
        plane = selector["plane"]
        axis = plane["axis"].upper()
        target = float(plane["value"])
        tolerance = float(plane["tolerance"])
        indices = []
        for index, face in enumerate(faces or (), 1):
            bounds = face.BoundBox
            low, high = getattr(bounds, axis + "Min"), getattr(bounds, axis + "Max")
            if abs(low - target) <= tolerance and abs(high - target) <= tolerance:
                indices.append(index)
    if not indices:
        raise ValueError("face selector matched no faces")
    if any(index > face_count for index in indices):
        raise ValueError("face index is out of range")
    return indices
