# SPDX-FileCopyrightText: 2026 Sjors Robroek
# SPDX-FileCopyrightText: James Dillard
#
# SPDX-License-Identifier: Apache-2.0

"""Kernel-neutral Booleans adapted from AgentCAD 0.7.0.

Adapted from AgentCAD 0.7.0 `agentcad.helpers` (`_run_safe_boolean`, `_canonical_occupied_shape`,
`_volume_slack`; Copyright James Dillard, Apache-2.0); see NOTICE and LICENSES/Apache-2.0.txt.
Rewritten as functions over raw `TopoDS_Shape` that run unchanged on OCP 7.9 and 8.0.

This file is the Apache-2.0 boundary: `occ` (MPL-2.0) re-exports these three functions. `occ` is
imported inside each function because `occ` imports this module while it loads.
"""

from __future__ import annotations

from typing import Any, Iterable

from cadmodel.motion import load_pose

FUZZY_MM = load_pose().FUZZY_MM  # the Boolean fuzzy value of cad-motion's interference rule


def boolean(operation: str, argument: Any, tools: Iterable[Any], fuzzy: float = FUZZY_MM) -> Any:
    """Non-destructive BOPAlgo Boolean of copies of the inputs; raises GeometryError on kernel errors."""
    from OCP.BOPAlgo import BOPAlgo_BOP, BOPAlgo_COMMON, BOPAlgo_CUT, BOPAlgo_FUSE

    from cadmodel import occ

    kinds = {"cut": BOPAlgo_CUT, "fuse": BOPAlgo_FUSE, "common": BOPAlgo_COMMON}
    op = BOPAlgo_BOP()
    shape_list = load_pose().shape_list
    op.SetArguments(shape_list([occ.copy(argument)]))
    op.SetTools(shape_list([occ.copy(tool) for tool in tools]))
    op.SetOperation(kinds[operation])
    op.SetNonDestructive(True)
    op.SetFuzzyValue(fuzzy)
    try:
        op.Perform()
    except Exception as error:  # noqa: BLE001 - OCP raises Standard_Failure subclasses
        raise occ.GeometryError(f"{operation} failed in the kernel: {error}") from error
    if op.HasErrors():
        detail = "; ".join(occ._messages(op, "DumpErrors")) or "unknown kernel error"
        raise occ.GeometryError(f"{operation} failed in the kernel: {detail}")
    result = op.Shape()
    if result.IsNull():
        raise occ.GeometryError(f"{operation} returned a null shape")
    return result


def occupied(shape: Any, fuzzy: float = FUZZY_MM) -> tuple[Any, float]:
    """(shape, volume) with overlapping solid members counted once.

    A multi-solid shape is partitioned by one General Fuse into non-overlapping cells, so a
    compound whose members overlap does not double-count the space they share.
    """
    from OCP.BOPAlgo import BOPAlgo_CellsBuilder

    from cadmodel import occ

    solids = occ.subshapes(shape, "SOLID")
    if len(solids) <= 1:
        return shape, occ.volume(shape)
    cells = BOPAlgo_CellsBuilder()
    cells.SetNonDestructive(True)
    cells.SetFuzzyValue(fuzzy)
    for solid in solids:
        cells.AddArgument(occ.copy(solid))
    try:
        cells.Perform()
    except Exception as error:  # noqa: BLE001
        raise occ.GeometryError(f"occupied-volume partition failed: {error}") from error
    if cells.HasErrors():
        raise occ.GeometryError("occupied-volume partition failed: " + ("; ".join(occ._messages(cells, "DumpErrors")) or "unknown"))
    cells.RemoveAllFromResult()
    cells.AddAllToResult()
    result = cells.Shape()
    if result.IsNull() or not occ.is_valid(result):
        raise occ.GeometryError("occupied-volume partition is not a valid B-rep")
    return result, occ.volume(result)


def volume_slack(*volumes: float) -> float:
    """Kernel noise allowed in a volume bound: 1e-7 relative, at least 1e-6 mm^3."""
    return max(1e-6, max((abs(v) for v in volumes), default=0.0) * 1e-7)
