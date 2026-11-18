# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""FreeCAD-side job of fea_solvers.probe: report the ccx, Gmsh and TechDraw templates this FreeCAD has.

freecad_runtime.run_job starts it under freecadcmd; CAD_DRAWING_JOB names its JSON job file, whose
`freecadcmd` is the command the host started and `module_dir` holds fea_solvers.py. The templates are
listed here because an AppImage's resource directory ends with this process.
"""

import json
import os
import sys
import traceback

job = json.load(open(os.environ["CAD_DRAWING_JOB"], encoding="utf-8"))
try:
    import FreeCAD as App

    sys.path.insert(0, job["module_dir"])
    from fea_solvers import resolve_in_freecad

    templates = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")
    files = sorted(os.path.relpath(os.path.join(root, name), templates).replace(os.sep, "/")
                   for root, _, names in os.walk(templates) for name in names)
    result = {"status": "pass",
              "freecad": {"path": os.path.realpath(sys.executable), "version": ".".join(App.Version()[:3])},
              "resource_dir": App.getResourceDir(), "techdraw_templates": {"dir": templates, "files": files},
              **resolve_in_freecad(job["freecadcmd"])}
except Exception as exc:  # noqa: BLE001 - every failure is reported in the result
    result = {"status": "error", "error": str(exc), "traceback": traceback.format_exc()}
with open(job["result"], "w", encoding="utf-8") as output:
    json.dump(result, output)
