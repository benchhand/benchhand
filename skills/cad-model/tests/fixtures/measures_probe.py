# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0"]
# ///
"""Measure shapes through `manufacture._Measures` and print the values and the OCCT passes as JSON.

Shapes: a 1 x 1 x 1 box asked for twice as two Python objects of one shape, a 2 x 3 x 4 box, the first
box moved 5 mm along X, and the first box reversed (a solid turned inside out).
Usage: uv run --script measures_probe.py  (stdout: {name: {volume, box}, "passes": {...}})
"""

import json
import sys
from pathlib import Path
from unittest import mock

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Trsf, gp_Vec
from OCP.TopLoc import TopLoc_Location

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from cadmodel import manufacture, occ  # noqa: E402

unit_maker = BRepPrimAPI_MakeBox(1.0, 1.0, 1.0)
unit, unit_again = unit_maker.Shape(), unit_maker.Shape()
shift = gp_Trsf()
shift.SetTranslation(gp_Vec(5.0, 0.0, 0.0))
shapes = {"unit": unit, "unit_again": unit_again, "big": BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Shape(),
          "moved": unit.Moved(TopLoc_Location(shift)), "reversed": unit.Reversed()}
passes = {"bbox": 0, "volume": 0}


def counted(name: str):
    real = getattr(occ, name)

    def measure(shape):
        passes[name] += 1
        return real(shape)
    return measure


report = {}
with mock.patch.object(occ, "bbox", counted("bbox")), mock.patch.object(occ, "volume", counted("volume")):
    measures = manufacture._Measures()
    for _ in range(2):  # the second round asks the memo again
        for name, shape in shapes.items():
            report[name] = {"volume": measures.volume(shape), "box": measures.box(shape)}
report["passes"] = passes
print(json.dumps(report))
