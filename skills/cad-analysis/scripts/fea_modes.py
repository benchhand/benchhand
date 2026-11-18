"""What a static FEA case reports in each mode, and how a fast case meshes.

`full`, the default, meshes curved second-order elements. It reports the peak von Mises stress, the peak
displacement and the yield safety factor, and a case's `required_safety_factor` decides its pass or fail.

`fast` meshes straight-sided second-order elements. Gmsh puts each mid-side node on the straight edge, with
its SecondOrderLinear option at 1, and runs no high-order pass. A fast case reports the peak displacement, which the
stiffness check reads, and a mesh-stability indicator: the p95 von Mises stress over all mesh nodes. It reports
no stress figure, safety factor or stress verdict. A peak and the stress away from supports did not settle
between meshes in the fast-bias campaign, so stress needs the separate stress study.

Standard library only: the FreeCAD job (Python 3.11) imports these names too.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

MODES = ("full", "fast")
DEFAULT = "full"
# Gmsh `Mesh.HighOrderOptimize` for a fast case: straight-sided elements need no curving, so level 0 at both steps.
FAST_HIGH_ORDER = {"optimize": 0, "fallback": 0}
P95_LABEL = ("mesh-stability indicator, not a strength value: the 95th-percentile von Mises stress over all mesh "
             "nodes. It shows whether the stress field settles as the mesh changes. It has no pass or fail and "
             "supports no strength, yield or safety-factor claim.")
STRESS_STUDY = ("fast mode reports no stress figure. Peak stress, the stress away from supports and the yield "
                "safety factor come only from the separate stress study, which compares two mesh sizes and excludes "
                "support and edge singularities.")


def percentile_95(values: Sequence[float]) -> float:
    """The p95 value of `values`: the value at rank position (n - 1) * 0.95 in ascending order, linear
    between the two ranks around it (statistics.quantiles, inclusive method)."""
    if len(values) == 1:
        return float(values[0])
    return statistics.quantiles(values, n=20, method="inclusive")[-1]


def reported(mode: str, *, von_mises: Sequence[float], displacement: Sequence[float], yield_mpa: float,
             required_safety_factor: float | None) -> dict[str, Any]:
    """The result fields of a completed solve in `mode`, from its nodal von Mises stress (MPa) and nodal
    displacement magnitude (mm): its measurements and its status."""
    max_displacement = max(displacement)
    if mode == "fast":
        return {"max_displacement_mm": max_displacement,
                "mesh_stability_indicator": {"p95_von_mises_mpa": percentile_95(von_mises), "nodes": len(von_mises),
                                             "label": P95_LABEL},
                "stress_study": STRESS_STUDY, "status": "pass"}
    max_stress = max(von_mises)
    factor = yield_mpa / max_stress if max_stress else None
    missed = required_safety_factor is not None and factor is not None and factor < required_safety_factor
    return {"max_von_mises_mpa": max_stress, "max_displacement_mm": max_displacement, "safety_factor_yield": factor,
            "required_safety_factor": required_safety_factor, "status": "fail" if missed else "pass"}
