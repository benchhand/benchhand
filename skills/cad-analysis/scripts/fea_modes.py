# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""What a static FEA case reports in each mode.

Both modes mesh curved second-order elements with the same Gmsh settings. `full`, the default, reports the
peak von Mises stress, the peak displacement and the yield safety factor, and a case's `required_safety_factor`
decides its pass or fail.

`fast` reports the peak displacement, which the stiffness check reads, and a mesh-stability indicator: the p95
von Mises stress over all mesh nodes. It reports no stress figure, safety factor or stress verdict. Its status
is the run's: `pass` means that the solver run completed, and `status_scope` says so. A peak and the stress away
from supports did not settle between meshes in the fast-bias campaign, so stress needs the separate stress study.

`stress` is that study. It solves two curved meshes, at the case's `mesh_size` h and at `STRESS_STEP_RATIO` x h,
and fea_stress compares their stress away from the supports and loads. Each of its two solves reports the peak
displacement and the run's status only: the stress comes from the solve's `.frd` file, and fea_stress_study
states the outcome.

Standard library only: the FreeCAD job (Python 3.11) imports these names too.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

MODES = ("full", "fast", "stress")
DEFAULT = "full"
# The fine mesh of a stress study is this fraction of the coarse mesh size: the r2-finer step of the fast-bias campaign.
STRESS_STEP_RATIO = 0.75
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


def reported(mode: str, *, von_mises: Sequence[float], displacement: Sequence[float], yield_mpa: float | None,
             required_safety_factor: float | None) -> dict[str, Any]:
    """The result fields of a completed solve in `mode`, from its nodal von Mises stress (MPa) and nodal
    displacement magnitude (mm): its measurements and its status. A case with no strength criterion resolves no
    yield (`yield_mpa` None) and reports no safety factor."""
    max_displacement = max(displacement)
    if mode == "stress":  # one solve of a stress study: its stress is read from the results file
        return {"max_displacement_mm": max_displacement, "status": "pass", "status_scope": "run"}
    if mode == "fast":
        return {"max_displacement_mm": max_displacement,
                "mesh_stability_indicator": {"p95_von_mises_mpa": percentile_95(von_mises), "nodes": len(von_mises),
                                             "label": P95_LABEL},
                "stress_study": STRESS_STUDY, "status": "pass", "status_scope": "run"}
    max_stress = max(von_mises)
    factor = yield_mpa / max_stress if max_stress and yield_mpa is not None else None
    missed = required_safety_factor is not None and factor is not None and factor < required_safety_factor
    return {"max_von_mises_mpa": max_stress, "max_displacement_mm": max_displacement, "safety_factor_yield": factor,
            "required_safety_factor": required_safety_factor, "status": "fail" if missed else "pass"}
