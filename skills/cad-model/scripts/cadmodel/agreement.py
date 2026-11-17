"""GLB <-> STEP agreement: whether each occurrence's mesh in assembly.glb depicts the STEP shape placed where
assembly.step places it. The two outputs come from one exporter run, so a stale, scaled, moved or
re-shaped mesh passes every check that reads either file alone.

The code turns both into triangle meshes in the assembly frame (mm, Z up): the world mesh of the GLB node, and a
finer tessellation of the STEP definition moved by the location of the component. The meshes agree when each
sampled point (a vertex or a triangle center) of one lies within the deflections of both meshes of the triangles
of the other. A point is within the deflection of its own mesh of the surface of the shape, and the other mesh
follows that surface within its own deflection. A mismatch beyond that is a different shape or place. The two
directions together see a feature that one mesh has and the other lacks.

An occurrence the data cannot judge (no deflection recorded, no node or triangles in the GLB, no shape) is
`unknown`, never `pass`. glTF is meters, Y up. The CAD frame is mm, Z up:
(x, y, z) -> (1000 x, -1000 z, 1000 y), the inverse of the exporter's own conversion.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:  # `check` also runs without numpy. The agreement then abstains
    np = None  # type: ignore[assignment]

AVAILABLE = np is not None
from cadmodel import occ
from cadmodel.mesh import ANGULAR_RAD

Vec = tuple[float, float, float]
Rot = tuple[Vec, Vec, Vec]
EPS_MM = 5e-3           # float32 coordinates and the exporter's own rounding, on top of the deflections
REFERENCE_FACTOR = 0.5  # the check tessellates the STEP reference at this fraction of the GLB deflection
REACH_MARGIN = 2.0      # the reach is this many times the sum of both deflections. On lamp-arm (0.05 mm GLB) the
                        # worst clean point sits at 1.19 of the sum (LMP-0004, a 1.2 mm fillet torus). The reach
                        # and EPS_MM leave a 1.74x margin. A bare sum would fail that clean part
MAX_SAMPLES = 1500      # points checked per direction and occurrence
CELLS = 8               # grid cells along the longest side of a mesh, for the triangle lookup
SAMPLE_CELLS = 48       # cells along the longest side of a mesh that each contribute a sample point
WORST_POINTS = 5        # failing points measured exactly, to report the distance
BATCH = 256            # query points against their candidate triangles at once
COMPONENTS = {5121: "<u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
TYPE_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}


class AgreementError(ValueError):
    """The GLB is not a glTF this reader understands."""


@dataclass(frozen=True, eq=False)
class Mesh:
    """Triangles by index: `points` (N, 3) in mm, `triangles` (M, 3) indices into them."""

    points: np.ndarray
    triangles: np.ndarray


@dataclass(frozen=True)
class Placement:
    """A definition (by PN) at a rigid location: x -> rot @ x + trans, rot as rows."""

    pn: str
    rot: Rot
    trans: Vec


# --- reading ---------------------------------------------------------------------------------------

def _accessor(gltf: Mapping[str, Any], binary: bytes, index: int) -> np.ndarray:
    accessor = gltf["accessors"][index]
    if "sparse" in accessor:
        raise AgreementError("sparse accessors are not read")
    view = gltf["bufferViews"][accessor["bufferView"]]
    dtype = np.dtype(COMPONENTS[accessor["componentType"]])
    width = TYPE_WIDTH[accessor["type"]]
    stride = view.get("byteStride") or dtype.itemsize * width
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"]
    raw = np.frombuffer(binary, dtype=np.uint8, count=stride * (count - 1) + dtype.itemsize * width, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(count, dtype.itemsize * width), strides=(stride, 1))
    return np.ascontiguousarray(rows).view(dtype).reshape(count, width)


def _local(node: Mapping[str, Any]) -> np.ndarray:
    """The own transform of the node as a 4x4 (row-major, acting on column vectors)."""
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    rotation = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation * np.array(node.get("scale", (1.0, 1.0, 1.0)))
    matrix[:3, 3] = node.get("translation", (0.0, 0.0, 0.0))
    return matrix


def _chunks(path: Path) -> tuple[dict[str, Any], bytes]:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise AgreementError(f"{path} is not a binary glTF file")
    length, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        raise AgreementError(f"{path} has no JSON chunk first")
    gltf: dict[str, Any] = json.loads(data[20:20 + length])
    cursor = 20 + length
    binary = b""
    if len(data) >= cursor + 8:
        size, kind = struct.unpack_from("<I4s", data, cursor)
        if kind == b"BIN\0":
            binary = data[cursor + 8:cursor + 8 + size]
    return gltf, binary


def read_glb(path: Path) -> dict[str, Mesh]:
    """Each named node that holds a mesh -> its triangles at the world pose of the node, in mm and Z up.
    The function merges nodes of one name into one mesh."""
    gltf, binary = _chunks(path)
    nodes = gltf.get("nodes", [])
    parent = {child: index for index, node in enumerate(nodes) for child in node.get("children", ())}
    cad_from_gltf = np.array([[1000.0, 0.0, 0.0], [0.0, 0.0, -1000.0], [0.0, 1000.0, 0.0]])
    found: dict[str, tuple[list[np.ndarray], list[np.ndarray]]] = {}
    for index, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        world = _local(node)
        at = index
        while at in parent:
            at = parent[at]
            world = _local(nodes[at]) @ world
        points, triangles = found.setdefault(node.get("name", f"node{index}"), ([], []))
        for primitive in gltf["meshes"][node["mesh"]].get("primitives", ()):
            if primitive.get("mode", 4) != 4:
                raise AgreementError("only triangle primitives are read")
            local = _accessor(gltf, binary, primitive["attributes"]["POSITION"]).astype(float)
            order = (_accessor(gltf, binary, primitive["indices"]).reshape(-1).astype(np.int64)
                     if "indices" in primitive else np.arange(len(local)))
            base = sum(len(p) for p in points)
            points.append((local @ world[:3, :3].T + world[:3, 3]) @ cad_from_gltf.T)
            triangles.append(base + order[:len(order) // 3 * 3].reshape(-1, 3))
    return {name: Mesh(np.concatenate(points), np.concatenate(triangles)) for name, (points, triangles) in found.items()}


# --- reference mesh of a STEP shape -----------------------------------------------------------------

def reference(shape: Any, linear_mm: float) -> Mesh:
    """`shape` tessellated at `linear_mm` into a mesh in its own frame (on a copy: a caller's shape keeps its mesh).
    The control-surface deflection is on for every surface type: without it BRepMesh leaves a torus fillet up to
    3 x `linear_mm` from the B-rep. The reach relies on the reference lying within `linear_mm`."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.IMeshTools import IMeshTools_Parameters
    from OCP.TopLoc import TopLoc_Location

    work = occ.copy(shape)
    parameters = IMeshTools_Parameters()
    parameters.Deflection = linear_mm
    parameters.Angle = ANGULAR_RAD
    parameters.Relative = False
    parameters.InParallel = True
    parameters.ControlSurfaceDeflection = True
    parameters.EnableControlSurfaceDeflectionAllSurfaces = True
    BRepMesh_IncrementalMesh(work, parameters)
    points: list[Vec] = []
    triangles: list[tuple[int, int, int]] = []
    for face in occ.subshapes(work, "FACE"):
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(occ.downcast(face, "FACE"), location)
        if triangulation is None:
            continue
        transform = location.Transformation()
        base = len(points)
        for i in range(1, triangulation.NbNodes() + 1):
            p = triangulation.Node(i).Transformed(transform)
            points.append((p.X(), p.Y(), p.Z()))
        for i in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(i).Get()
            triangles.append((base + a - 1, base + b - 1, base + c - 1))
    if not triangles:
        return Mesh(np.empty((0, 3)), np.empty((0, 3), dtype=np.int64))
    return Mesh(np.array(points), np.array(triangles, dtype=np.int64))


def placed(mesh: Mesh, rot: Rot, trans: Vec) -> Mesh:
    """`mesh` moved by x -> rot @ x + trans."""
    return Mesh(mesh.points @ np.array(rot).T + np.array(trans), mesh.triangles)


# --- distance ---------------------------------------------------------------------------------------

def _dot(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.einsum("ij,ij->i", u, v)


def _segment_sq(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Squared distance from points p (k, 3) to the segments a-b (k, 3), pair by pair."""
    edge = b - a
    length = _dot(edge, edge)
    along = np.clip(_dot(p - a, edge) / np.where(length > 0, length, 1.0), 0.0, 1.0)
    offset = p - a - along[:, None] * edge
    return _dot(offset, offset)


def _triangle_sq(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Squared distance from points p (k, 3) to the triangles abc (k, 3 each), pair by pair."""
    ab, ac, ap = b - a, c - a, p - a
    d00, d01, d11 = _dot(ab, ab), _dot(ab, ac), _dot(ac, ac)
    d20, d21 = _dot(ap, ab), _dot(ap, ac)
    denominator = d00 * d11 - d01 * d01
    live = denominator > 1e-18
    safe = np.where(live, denominator, 1.0)
    v = (d11 * d20 - d01 * d21) / safe
    w = (d00 * d21 - d01 * d20) / safe
    inside = live & (v >= 0) & (w >= 0) & (v + w <= 1)
    offset = ap - v[:, None] * ab - w[:, None] * ac
    edges = np.minimum(np.minimum(_segment_sq(p, a, b), _segment_sq(p, b, c)), _segment_sq(p, c, a))
    return np.where(inside, _dot(offset, offset), edges)


class _Surface:
    """A mesh's triangles in a uniform grid of cells at least twice `reach` wide, so a point's reach ball lies in
    the 27 cells around its own."""

    def __init__(self, mesh: Mesh, reach: float) -> None:
        self.reach = reach
        self.corners = mesh.points[mesh.triangles]  # (M, 3 corners, 3)
        self.box_low, self.box_high = self.corners.min(axis=1) - reach, self.corners.max(axis=1) + reach
        low = mesh.points.min(axis=0)
        self.origin = low
        self.cell = max(2.0 * reach, float((mesh.points.max(axis=0) - low).max()) / CELLS, 1e-9)
        lows, highs = self._index(self.corners.min(axis=1)).tolist(), self._index(self.corners.max(axis=1)).tolist()
        self.cells: dict[tuple[int, int, int], list[int]] = {}
        for number, (lo, hi) in enumerate(zip(lows, highs)):
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        self.cells.setdefault((x, y, z), []).append(number)

    def _index(self, points: np.ndarray) -> np.ndarray:
        return np.floor((points - self.origin) / self.cell).astype(np.int64)

    def within(self, points: np.ndarray) -> np.ndarray:
        """Per point: whether a triangle lies within `reach` of it."""
        result = np.zeros(len(points), dtype=bool)
        groups: dict[tuple[int, int, int], list[int]] = {}
        for number, key in enumerate(map(tuple, self._index(points).tolist())):
            groups.setdefault(key, []).append(number)
        limit = self.reach * self.reach
        for (cx, cy, cz), members in groups.items():
            near = {t for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                    for t in self.cells.get((cx + dx, cy + dy, cz + dz), ())}
            if not near:
                continue
            near = np.array(sorted(near))
            for start in range(0, len(members), BATCH):
                chosen = np.array(members[start:start + BATCH])
                p = points[chosen]
                box = np.all((p[:, None, :] >= self.box_low[near][None]) & (p[:, None, :] <= self.box_high[near][None]),
                             axis=2)  # triangles whose reach-grown box holds the point
                point, triangle = np.nonzero(box)
                if not len(point):
                    continue
                corners = self.corners[near[triangle]]
                squared = _triangle_sq(p[point], corners[:, 0], corners[:, 1], corners[:, 2])
                result[chosen[point[squared <= limit]]] = True
        return result

    def nearest(self, point: np.ndarray) -> float:
        """The distance to the nearest triangle anywhere (reported for a failing point only)."""
        p = np.broadcast_to(point, (len(self.corners), 3))
        squared = _triangle_sq(p, self.corners[:, 0], self.corners[:, 1], self.corners[:, 2])
        return math.sqrt(float(squared.min()))


def _samples(mesh: Mesh) -> np.ndarray:
    """Up to MAX_SAMPLES points on the mesh, spread over it: its vertices and triangle centers, the first of each
    grid cell, thinned by an even stride when there are more. A vertex at the rim of a straight wall lies on the
    plane beside it. The triangle center of the wall lies in the wall, where a hole the other mesh lacks shows."""
    used = np.unique(mesh.triangles)
    found = np.concatenate([mesh.points[used], mesh.points[mesh.triangles].mean(axis=1)])
    low = found.min(axis=0)
    span = float((found.max(axis=0) - low).max()) / SAMPLE_CELLS or 1.0
    cells = np.floor((found - low) / span).astype(np.int64)
    _, first = np.unique(cells, axis=0, return_index=True)
    chosen = np.sort(first)
    if len(chosen) > MAX_SAMPLES:
        chosen = chosen[::math.ceil(len(chosen) / MAX_SAMPLES)]
    return found[chosen]


def _off(points: np.ndarray, surface: _Surface) -> tuple[float, int]:
    """(the largest distance of the points beyond `surface.reach`, how many). (0.0, 0) when all are within."""
    failing = points[~surface.within(points)]
    if not len(failing):
        return 0.0, 0
    return max(surface.nearest(p) for p in failing[:WORST_POINTS]), len(failing)


def compare(glb: Mesh, step: Mesh, linear_mm: float) -> dict[str, Any]:
    """Whether `glb` and `step` (both in the assembly frame) are one shape. A sample of one mesh is within its own
    deflection of the true surface and the other mesh within its: `linear_mm` for the GLB, REFERENCE_FACTOR of
    it for the STEP reference. Their sum bounds the distance of the sample to the other mesh. REACH_MARGIN times
    that sum plus EPS_MM is the reach."""
    reach = REACH_MARGIN * linear_mm * (1.0 + REFERENCE_FACTOR) + EPS_MM
    if not len(glb.triangles) or not len(step.triangles):
        return {"status": "unknown", "tolerance_mm": reach,
                "reason": f"the {'GLB mesh' if not len(glb.triangles) else 'STEP shape'} has no triangles to compare"}
    glb_off, glb_count = _off(_samples(glb), _Surface(step, reach))
    step_off, step_count = _off(_samples(step), _Surface(glb, reach))
    ok = glb_count == 0 and step_count == 0
    row: dict[str, Any] = {"status": "pass" if ok else "fail", "tolerance_mm": reach,
                           "glb_to_step_mm": glb_off, "step_to_glb_mm": step_off,
                           "glb_points_off": glb_count, "step_points_off": step_count}
    if not ok:
        row["reason"] = ("the GLB mesh and the STEP shape differ beyond the exporter's deflection: "
                         f"{glb_count} GLB and {step_count} STEP sample points are farther than {reach:.3f} mm "
                         "from the other's surface")
    return row


def occurrences(glb: Path, nodes: Mapping[str, str], definitions: Mapping[str, Any],
                placements: Mapping[str, Placement], linear_mm: float | None) -> dict[str, dict[str, Any]]:
    """Per occurrence id: `compare` of its GLB node (`nodes`: id -> node name) with its definition at its placement.
    `unknown` when the version records no deflection, or the occurrence has no node, mesh or shape."""
    meshes = read_glb(glb)
    references: dict[str, Mesh] = {}
    rows: dict[str, dict[str, Any]] = {}
    for oid in sorted(placements):
        place = placements[oid]
        node = meshes.get(nodes.get(oid, ""))
        if linear_mm is None:
            rows[oid] = {"status": "unknown", "reason": "the version records no GLB deflection to judge the mesh by"}
        elif node is None or not len(node.triangles):
            rows[oid] = {"status": "unknown", "reason": "the GLB has no mesh node for this occurrence"}
        elif place.pn not in definitions:
            rows[oid] = {"status": "unknown", "reason": f"the STEP has no shape for {place.pn}"}
        else:
            if place.pn not in references:
                references[place.pn] = reference(definitions[place.pn], linear_mm * REFERENCE_FACTOR)
            rows[oid] = compare(node, placed(references[place.pn], place.rot, place.trans), linear_mm)
    return rows
