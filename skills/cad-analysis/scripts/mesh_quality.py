"""FreeCAD-independent validity and quality of a quadratic tetrahedral (C3D10) mesh.

The FreeCAD job writes its Gmsh mesh as an Abaqus/CalculiX input file and reads it back here, in ccx's
C3D10 node order: corners 1 to 4, then the mid-edge nodes of edges 1-2, 2-3, 3-1, 1-4, 2-4 and 3-4.

An element's Jacobian determinant is sampled at its ten nodes and at the four integration points of
ccx's C3D10. The element is invalid when any sample is zero or negative (ccx stops on a nonpositive
Jacobian). Its scaled Jacobian is the smallest sample divided by the largest sample magnitude: 1 for a
straight-sided tetrahedron, -1 for one inverted everywhere, whatever its size. Sampling is not a bound: an
element can be invalid between samples.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

Vector = tuple[float, float, float]
# Mid-edge nodes 5 to 10 in ccx's C3D10 order, as pairs of 0-based corner indices.
EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
_A, _B = 0.138196601125011, 0.585410196624968  # ccx's 4-point C3D10 integration rule
# Barycentric coordinates (L1, L2, L3, L4) of the corners, the mid-edge nodes and the integration points
SAMPLES: tuple[tuple[float, ...], ...] = (
    (1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0),
    *(tuple(0.5 if k in edge else 0.0 for k in range(4)) for edge in EDGES),
    (_B, _A, _A, _A), (_A, _B, _A, _A), (_A, _A, _B, _A), (_A, _A, _A, _B),
)
# d(L1..L4)/d(xi, eta, zeta) with L1 = 1 - xi - eta - zeta, L2 = xi, L3 = eta, L4 = zeta
_DL = ((-1.0, -1.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def jacobian_determinants(nodes: Sequence[Vector]) -> list[float]:
    """The Jacobian determinant of one C3D10 element at each of SAMPLES."""
    if len(nodes) != 10:
        raise ValueError(f"a C3D10 element has 10 nodes, not {len(nodes)}")
    values = []
    for weights in SAMPLES:
        gradients = [tuple((4.0 * weights[i] - 1.0) * d for d in _DL[i]) for i in range(4)]
        gradients += [tuple(4.0 * (weights[a] * _DL[b][c] + weights[b] * _DL[a][c]) for c in range(3))
                      for a, b in EDGES]
        j = [[sum(nodes[n][row] * gradients[n][col] for n in range(10)) for col in range(3)] for row in range(3)]
        values.append(j[0][0] * (j[1][1] * j[2][2] - j[1][2] * j[2][1])
                      - j[0][1] * (j[1][0] * j[2][2] - j[1][2] * j[2][0])
                      + j[0][2] * (j[1][0] * j[2][1] - j[1][1] * j[2][0]))
    return values


def scaled_jacobian(nodes: Sequence[Vector]) -> float:
    """Smallest sampled Jacobian determinant over the largest magnitude, in [-1, 1]: dimensionless, so an element
    and the same element scaled report one value, and negative exactly when the element is inverted somewhere."""
    values = jacobian_determinants(nodes)
    largest = max(abs(value) for value in values)
    return min(values) / largest if largest > 0 else 0.0


def read_c3d10(text: str) -> tuple[dict[int, Vector], list[tuple[int, ...]]]:
    """The nodes and C3D10 elements of an Abaqus/CalculiX input file; other element types are skipped."""
    nodes: dict[int, Vector] = {}
    elements: list[tuple[int, ...]] = []
    section = None
    pending: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            keyword = line.upper().replace(" ", "")
            section = ("node" if keyword.startswith("*NODE") and not keyword.startswith("*NODEPRINT")
                       else "c3d10" if keyword.startswith("*ELEMENT") and re.search(r"TYPE=C3D10\b", keyword)
                       else None)
            pending = []
            continue
        if section == "node":
            fields = [field for field in line.split(",") if field.strip()]
            nodes[int(fields[0])] = (float(fields[1]), float(fields[2]), float(fields[3]))
        elif section == "c3d10":
            pending += [field for field in line.split(",") if field.strip()]
            if len(pending) >= 11:
                elements.append(tuple(int(field) for field in pending[:11]))
                pending = []
    return nodes, elements


def mesh_quality(nodes: Mapping[int, Vector], elements: Iterable[Sequence[int]]) -> dict[str, Any]:
    """Element count, invalid-element count and the smallest scaled Jacobian of a C3D10 mesh."""
    count, invalid, smallest = 0, 0, None
    for element in elements:
        value = scaled_jacobian([nodes[node] for node in element[1:]])
        count += 1
        invalid += value <= 0
        smallest = value if smallest is None or value < smallest else smallest
    return {"elements": count, "invalid_elements": invalid, "min_scaled_jacobian": smallest}


def needs_fallback(quality: Mapping[str, Any], level: int, fallback: int) -> bool:
    """Whether a mesh made at high-order `level` must be remeshed at `fallback`: it has no element or an
    invalid one. Quality alone never triggers a remesh. The fallback level is terminal: its mesh is never
    remeshed; it is kept with its quality recorded when valid, and the job refuses it when invalid."""
    if level == fallback:
        return False
    return not quality.get("elements") or bool(quality.get("invalid_elements"))
