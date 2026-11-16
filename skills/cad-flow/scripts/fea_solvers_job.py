"""FreeCAD-side job of fea_solvers.probe: report the ccx and Gmsh this FreeCAD resolves.

freecad_runtime.run_job starts it under freecadcmd; CAD_DRAWING_JOB names its JSON job file, whose
`freecadcmd` is the command the host started and `module_dir` holds fea_solvers.py.
"""

import json
import os
import sys
import traceback

import FreeCAD as App

job = json.load(open(os.environ["CAD_DRAWING_JOB"], encoding="utf-8"))
try:
    sys.path.insert(0, job["module_dir"])
    from fea_solvers import resolve_in_freecad

    result = {"status": "pass",
              "freecad": {"path": os.path.realpath(sys.executable), "version": ".".join(App.Version()[:3])},
              "resource_dir": App.getResourceDir(), **resolve_in_freecad(job["freecadcmd"])}
except Exception as exc:  # noqa: BLE001 - every failure is reported in the result
    result = {"status": "error", "error": str(exc), "traceback": traceback.format_exc()}
with open(job["result"], "w", encoding="utf-8") as output:
    json.dump(result, output)
