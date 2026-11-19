# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The stress study's wiring: two solves' results in, the study's result fields out.

`fea_stress` holds the comparison (the exclusion distance, the node floors, the convergence rules). This module
only reads what a solve left in its run directory and maps what it finds to `stress_study`, the mapping the
result carries:

* `GmshMesh.frd` gives each node's coordinates, von Mises stress and displacement (`fea_stress.read_frd`).
* `GmshMesh.inp`, the ccx input FreeCAD wrote, gives the fixed-face nodes (`*NSET,NSET=FixedFaces`) and the
  load-face nodes (every node in a `*CLOAD` block, a zero load included).
* The solve's recorded mesh quality gives its high-order fallback level.

`element_connectivity` reads the C3D4 and C3D10 elements of the same input and `load_blocks` the `*CLOAD` forces, for the
linearisation of #209 (`fea_near_support`, which `fea_static` calls for a `settled_away_from_support` study).

The outcome is `settled` (an interior peak: strength-eligible), `settled_away_from_support` (the peak sits on the
exclusion boundary, where the stress near the support is higher: information only, see #209) or `unknown`. A
result the study cannot read gives `unknown` with a named reason. It never raises and never reports a number it
cannot stand behind.

Standard library only apart from fea_stress, which is too.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fea_modes
import fea_stress as fs

FRD, INP = "GmshMesh.frd", "GmshMesh.inp"
SEE_209 = ("the stress near the support is higher than this figure: it comes only from linearisation at the "
           "support (#209)")


class BoundaryNodesError(ValueError):
    """The ccx input holds no usable fixed-face or load-face node set."""


@dataclass(frozen=True)
class SolveRun:
    """One solve of the study: its run directory, its mesh size and the high-order fallback level it used."""

    run_dir: Path
    h: float
    high_order_fallback: int | None


def fallback_level(quality: dict[str, Any]) -> int | None:
    """The high-order optimisation level the mesh was remeshed at, or None when the first level was kept."""
    return quality.get("high_order_optimize") if quality.get("rejected") is not None else None


_NODE = re.compile(r"[0-9]+")  # ASCII digits: ccx writes no others


def boundary_nodes(inp: str) -> tuple[frozenset[int], frozenset[int]]:
    """The fixed-face and load-face node ids of a FreeCAD-written ccx input.

    FreeCAD writes the fixed faces as one `*NSET,NSET=FixedFaces` block of `id,` lines and each force constraint
    as a `*CLOAD` block of `node, degree of freedom, load` lines; `**` lines are comments. Raises
    `BoundaryNodesError` when either set is empty or a line is not that form.
    """
    fixed: set[int] = set()
    load: set[int] = set()
    target: set[int] | None = None
    for raw in inp.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        keyword = line.upper()
        if keyword.startswith("*"):
            target = fixed if keyword.startswith("*NSET,NSET=FIXEDFACES") else load if keyword.startswith("*CLOAD") else None
            continue
        if target is None:
            continue
        field = line.split(",")[0].strip()
        try:
            if _NODE.fullmatch(field) is None:
                raise ValueError(field)
            target.add(int(field))  # int() also refuses a string of more than 4300 digits
        except ValueError:
            raise BoundaryNodesError(f"{line[:40]!r} is not a node record") from None
    if not fixed or not load:
        raise BoundaryNodesError(f"the ccx input names {len(fixed)} fixed-face and {len(load)} load-face nodes")
    return frozenset(fixed), frozenset(load)


class ConnectivityError(ValueError):
    """The ccx input holds no usable C3D4 or C3D10 element connectivity."""


def _force_entry(line: str) -> tuple[int, int, float]:
    """The node, the degree of freedom (0 to 2) and the force of one `*CLOAD` line."""
    fields = [f.strip() for f in line.split(",")]
    try:
        if len(fields) != 3 or _NODE.fullmatch(fields[0]) is None or fields[1] not in ("1", "2", "3"):
            raise ValueError(line)
        force = float(fields[2])
        if not math.isfinite(force):
            raise ValueError(line)
        return int(fields[0]), int(fields[1]) - 1, force
    except ValueError:
        raise BoundaryNodesError(f"{line[:40]!r} is not a node force record") from None


def load_blocks(inp: str) -> list[dict[int, tuple[float, float, float]]]:
    """The nodal forces of each `*CLOAD` block of a FreeCAD-written ccx input: one dict per block, node to (fx, fy, fz).

    FreeCAD writes one block per force constraint, so a block is one load face. A node's zero load is kept, as
    `boundary_nodes` counts it a load node. Raises `BoundaryNodesError` when a line is not a `node, 1 to 3, force`
    record or a node and degree of freedom repeats in one block.
    """
    blocks: list[dict[int, list[float]]] = []
    entered: set[tuple[int, int]] = set()
    inside = False
    for raw in inp.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            inside = line.upper().startswith("*CLOAD")
            if inside:
                blocks.append({})
                entered = set()
            continue
        if not inside:
            continue
        node, dof, force = _force_entry(line)
        if (node, dof) in entered:
            raise BoundaryNodesError(f"node {node} has two forces on degree of freedom {dof + 1}")
        entered.add((node, dof))
        blocks[-1].setdefault(node, [0.0, 0.0, 0.0])[dof] = force
    return [{n: (c[0], c[1], c[2]) for n, c in block.items()} for block in blocks if block]


# Nodes per element of each solid type `element_connectivity` reads. The ccx 2.23 manual numbers them (sections
# 6.2.6 and 6.2.7; Figure 63 for C3D10): the four corners, then for C3D10 the mid-edge nodes of edges 1-2, 2-3, 3-1,
# 1-4, 2-4 and 3-4. *ELEMENT continues a record only past 16 entries, so neither type has a continuation line.
ELEMENT_NODES = {"C3D4": 4, "C3D10": 10}


@dataclass(frozen=True)
class Element:
    """One solid element of a ccx input: its type `kind` (C3D4 or C3D10) and its node ids in the ccx manual's order."""

    kind: str
    nodes: tuple[int, ...]


def _element_type(keyword: str) -> str | None:
    """The TYPE of an `*ELEMENT` keyword line (blanks removed, upper case), or None for another keyword."""
    name, *parameters = keyword.split(",")
    if name != "*ELEMENT":
        return None
    types = [value for key, _, value in (p.partition("=") for p in parameters) if key == "TYPE"]
    if not types:
        raise ConnectivityError(f"the *ELEMENT block has no TYPE: {keyword[:40]!r}")
    if types[0] not in ELEMENT_NODES:
        raise ConnectivityError(f"element type {types[0][:20]} is not C3D4 or C3D10")
    return types[0]


def _element(line: str, kind: str) -> tuple[int, tuple[int, ...]]:
    """The id and the node ids of one element record of type `kind`."""
    fields = [f.strip() for f in line.split(",")]
    try:
        if not all(_NODE.fullmatch(f) for f in fields):
            raise ValueError(line)
        element, *nodes = (int(f) for f in fields)  # int() also refuses a string of more than 4300 digits
    except ValueError:
        raise ConnectivityError(f"{line[:40]!r} is not a {kind} element record") from None
    if len(nodes) != ELEMENT_NODES[kind]:
        raise ConnectivityError(f"element {element} has {len(nodes)} of {ELEMENT_NODES[kind]} nodes")
    return element, tuple(nodes)


def element_connectivity(inp: str) -> dict[int, Element]:
    """The C3D4 and C3D10 elements of a ccx input, keyed by element id.

    An element block is an `*ELEMENT,TYPE=...` keyword line, case and blanks aside, then one `id, node, ...` line per
    element; `**` lines are comments. Raises `ConnectivityError` when the input has no such element, when a block
    has no TYPE or another one, when a record is not that form or has the wrong number of nodes, or when an element
    id repeats.
    """
    elements: dict[int, Element] = {}
    kind: str | None = None
    for raw in inp.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            kind = _element_type(line.upper().replace(" ", ""))
            continue
        if kind is None:
            continue
        element, nodes = _element(line, kind)
        if element in elements:
            raise ConnectivityError(f"element {element} is defined twice")
        elements[element] = Element(kind, nodes)
    if not elements:
        raise ConnectivityError("the ccx input defines no C3D4 or C3D10 elements")
    return elements


def _number(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _level_fields(peak: fs.LevelPeak) -> dict[str, Any]:
    return {"sigma_kept_mpa": _number(peak.sigma_kept),
            "hot_spot": None if peak.hot_spot is None else list(peak.hot_spot),
            "kept_nodes": peak.kept_count, "nodes": peak.node_count,
            "max_displacement_mm": _number(peak.max_displacement)}


def _unknown(coarse: SolveRun, fine: SolveRun, reason: str, message: str) -> dict[str, Any]:
    sizes = f"h_coarse={coarse.h:g} mm h_fine={fine.h:g} mm"
    return {"outcome": fs.UNKNOWN, "strength_eligible": False, "sigma_mpa": None, "sigma_info_mpa": None,
            "reasons": [reason], "message": f"UNKNOWN ({reason}): {message}; {sizes}",
            "mesh_sizes_mm": {"coarse": coarse.h, "fine": fine.h}, "step_ratio": fea_modes.STRESS_STEP_RATIO}


def _read_level(label: str, run: SolveRun) -> fs.Level:
    """The comparison input of one solve.

    Raises FrdError (also for a `.frd` that is not UTF-8 text), BoundaryNodesError (also for a ccx input that
    is not UTF-8 text) or OSError, which covers a missing file, a directory and a file that cannot be read.
    """
    result = fs.read_frd(_text(run.run_dir / FRD, fs.FrdError))
    fixed, load = boundary_nodes(_text(run.run_dir / INP, BoundaryNodesError))
    return fs.Level(run.h, result, fixed, load, run.high_order_fallback)


def _text(path: Path, error: type[ValueError]) -> str:
    """The UTF-8 text of `path`; a decoding failure raises `error`, which names the file."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise error(f"{path.name} is not UTF-8 text ({exc.reason} at byte {exc.start})") from None


def run_study(coarse: SolveRun, fine: SolveRun) -> dict[str, Any]:
    """The `stress_study` result fields from the coarse and the fine solve."""
    sizes_valid = all(math.isfinite(run.h) and run.h > 0 for run in (coarse, fine))
    if sizes_valid and not coarse.h > fine.h:
        # compare_levels raises ValueError for a valid pair in the wrong order: a caller error that must not crash.
        return _unknown(coarse, fine, "mesh_sizes_not_ordered", "the coarse mesh size must exceed the fine one")
    levels: dict[str, fs.Level] = {}
    for label, run in (("coarse", coarse), ("fine", fine)):
        try:
            levels[label] = _read_level(label, run)
        except fs.FrdError as exc:
            return _unknown(coarse, fine, "malformed_frd", f"{label} mesh: {exc}")
        except BoundaryNodesError as exc:
            return _unknown(coarse, fine, "boundary_nodes_unreadable", f"{label} mesh: {exc}")
        except OSError as exc:
            return _unknown(coarse, fine, "results_file_missing", f"{label} mesh: {exc.filename or exc}")
    found = fs.compare_levels(levels["coarse"], levels["fine"])
    fields: dict[str, Any] = {
        "outcome": found.status, "strength_eligible": found.strength_eligible,
        "sigma_mpa": _number(found.sigma_mpa), "sigma_info_mpa": _number(found.sigma_info_mpa),
        "reasons": list(found.reasons), "message": found.message,
        "mesh_sizes_mm": {"coarse": coarse.h, "fine": fine.h}, "step_ratio": fea_modes.STRESS_STEP_RATIO,
        "delta_sigma": _number(found.delta_sigma), "delta_displacement": _number(found.delta_displacement),
        "hot_spot_shift_mm": _number(found.hot_spot_shift), "cutoff_mm": _number(found.details.get("cutoff_mm")),
        "coarse": _level_fields(found.coarse), "fine": _level_fields(found.fine),
    }
    if found.status == fs.SETTLED_AWAY_FROM_SUPPORT:
        fields["note"] = SEE_209
    return fields
