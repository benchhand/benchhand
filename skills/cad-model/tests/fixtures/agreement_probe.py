# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["cadquery-ocp-novtk==8.0.1.1.0", "pyyaml>=6.0", "numpy>=1.26"]
# ///
"""Run `cadmodel.agreement` on shapes built here and on a GLB written here; print the verdicts as JSON.

Every case compares a GLB mesh (the tessellation of one shape at the exporter's deflection) with the
STEP shape it should depict (a second shape, or the same one). Usage: uv run --script agreement_probe.py DIR
"""

import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from cadmodel import agreement  # noqa: E402

LINEAR = 0.1  # the exporter's deflection in these cases, mm
EMPTY_POINTS, EMPTY_TRIANGLES = np.empty((0, 3)), np.empty((0, 3), dtype=np.int64)
ROT = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def block(size: float = 40.0, height: float = 20.0, hole_x: float | None = 0.0, hole_d: float = 6.0) -> Any:
    """A size x size x height block centered on the Z axis with a through hole of diameter hole_d at (hole_x, 0)."""
    body = BRepPrimAPI_MakeBox(gp_Pnt(-size / 2, -size / 2, 0), size, size, height).Shape()
    if hole_x is None:
        return body
    axis = gp_Ax2(gp_Pnt(hole_x, 0, 0), gp_Dir(0, 0, 1))
    return BRepAlgoAPI_Cut(body, BRepPrimAPI_MakeCylinder(axis, hole_d / 2, height).Shape()).Shape()


def round_block(height: float = 20.0) -> Any:
    """A cylinder of the 40 x 40 block's plan area and height, with the same 6 mm hole: the same volume."""
    radius = 40.0 / math.sqrt(math.pi)
    body = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), radius, height).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 3.0, height).Shape()
    return BRepAlgoAPI_Cut(body, hole).Shape()


def moved(mesh: Any, dx: float) -> Any:
    return agreement.placed(mesh, ROT, (dx, 0.0, 0.0))


def verdict(drawn: Any, depicted: Any, *, shift: float = 0.0) -> dict[str, Any]:
    mesh = moved(agreement.reference(drawn, LINEAR), shift)
    return agreement.compare(mesh, agreement.reference(depicted, LINEAR / 4), LINEAR)


def cases() -> dict[str, Any]:
    clean = block()
    return {
        "same_shape": verdict(clean, clean),
        "equal_volume_cylinder": verdict(round_block(), clean),
        "shifted_3_mm": verdict(clean, clean, shift=3.0),
        "hole_moved_8_mm": verdict(block(hole_x=8.0), clean),
        "hole_missing_in_mesh": verdict(block(hole_x=None), clean),
        "extra_hole_in_mesh": verdict(clean, block(hole_x=None)),
        "hole_6_vs_6_5": verdict(block(hole_d=6.5), clean),
        "mesh_empty": agreement.compare(agreement.Mesh(EMPTY_POINTS, EMPTY_TRIANGLES), agreement.reference(clean, LINEAR), LINEAR),
        "step_empty": agreement.compare(agreement.reference(clean, LINEAR), agreement.Mesh(EMPTY_POINTS, EMPTY_TRIANGLES), LINEAR),
        "thousand_times_larger": agreement.compare(
            agreement.Mesh(agreement.reference(clean, LINEAR).points * 1000, agreement.reference(clean, LINEAR).triangles),
            agreement.reference(clean, LINEAR), LINEAR),
    }


def write_glb(path: Path, nodes: list[dict[str, Any]], mesh_index: int | None) -> None:
    """A one-triangle glTF: points (0,0,0), (0.001,0,0), (0,0.001,0) meters in mesh 0."""
    points = struct.pack("<9f", 0, 0, 0, 0.001, 0, 0, 0, 0.001, 0)
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes,
        "meshes": [{"name": "TST", "primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        "bufferViews": [{"buffer": 0, "byteLength": len(points)}], "buffers": [{"byteLength": len(points)}]}
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(points), b"BIN\0") + points
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def glb_case(folder: Path) -> dict[str, Any]:
    """A parent translated (0.01, 0.02, 0.03) m holding a child turned +90 degrees about glTF Y that holds the mesh."""
    quarter = math.sin(math.pi / 4)
    write_glb(folder / "nested.glb",
              [{"name": "outer", "translation": [0.01, 0.02, 0.03], "children": [1]},
               {"name": "inner", "rotation": [0.0, quarter, 0.0, quarter], "mesh": 0}], 0)
    read = agreement.read_glb(folder / "nested.glb")
    return {"names": sorted(read), "points": read["inner"].points.tolist(), "triangles": read["inner"].triangles.tolist()}


def abstentions(folder: Path) -> dict[str, Any]:
    """`occurrences` on the nested GLB: what it cannot judge it calls unknown, and says why."""
    shape = block()
    place = agreement.Placement
    placements = {"has_node": place("X", ROT, (0.0, 0.0, 0.0)), "no_node": place("X", ROT, (0.0, 0.0, 0.0)),
                  "no_shape": place("Y", ROT, (0.0, 0.0, 0.0))}
    nodes = {"has_node": "inner", "no_shape": "inner", "no_node": "absent"}
    glb = folder / "nested.glb"
    rows = agreement.occurrences(glb, nodes, {"X": shape}, placements, LINEAR)
    undeflected = agreement.occurrences(glb, nodes, {"X": shape}, placements, None)
    return {"rows": rows, "no_deflection": {oid: row["status"] for oid, row in undeflected.items()}}


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    glb = glb_case(folder)
    print(json.dumps({"cases": cases(), "glb": glb, "abstentions": abstentions(folder)}))
