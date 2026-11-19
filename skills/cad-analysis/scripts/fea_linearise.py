# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Stress linearisation along a stress classification line (SCL), after ASME BPVC VIII-2 Annex 5-A.

An SCL is a straight line through a wall, from `start` on one surface to `end` on the other. The method splits
the stress along it into membrane, bending and peak parts:

* The stress at each of `points` equally spaced points comes from the containing C3D4 or C3D10 element. The
  nodal tensor is interpolated with the element's linear or quadratic shape functions, at the natural
  coordinates its own map gives, so a curved C3D10 is inverted through its quadratic map.
* Each tensor is rotated to the SCL axes: the normal `n` along the line and two tangential axes `t1` and `t2`.
* The membrane stress is the mean of each component along the line (Annex 5-A, Eq 5-A.1).
* The bending stress is the first moment of each component about the mid-point, 6/t^2 times the integral of
  sigma (t/2 - x) (Eq 5-A.2), where x runs from 0 at `start` to t at `end`. Annex 5-A bends only the two
  tangential normal components (the hoop and meridional stresses of a vessel wall). The normal stress and
  the three shears are membrane plus peak only.
* Both integrals use the composite Simpson rule, which is exact for the quadratic stress of a C3D10 and the
  cubic integrand of its bending moment. Simpson needs an odd number of points.
* The peak stress at each end is the total there less the membrane and bending stress.
* Each von Mises stress comes from a linearised tensor. Von Mises itself is never linearised, because it is
  not a linear function of the components.

The tangent basis is a convention, and it matters: the bending of the in-plane shear is dropped, so the von
Mises stress of membrane plus bending depends on which two tangential axes carry the bending. `t1` is the part
of the caller's `tangent` normal to the line, made a unit vector. With no `tangent`, `t1` is the part of the
global axis least aligned with the line (the first of x, y and z on a tie). `t2 = n x t1`, so (t1, t2, n) is
right-handed. For a vessel or a beam wall the caller passes the meridional or the beam axis as `tangent`.

This module computes the linearised stresses only. It gives no verdict and applies no 1.5 shape-factor
credit: the caller checks strength at first yield of the linearised stress, and keeps the peak stress, as
the interior two-mesh study does (#209, decisions 2 and 4). Placing and classifying an SCL needs engineering
judgement (VIII-2 5.2.1.2), so the caller chooses the line.

Sources: the ASME VIII-2 and EN 13445-3 texts are paywalled, so the clause numbers come from secondary
sources: Kezar Engineering's summary of Annex 5-A, Mackenzie (PVP2017) and Kalnins (PVP2008). EN 13445-3
Annex C (C.4.4) states the same split. Kalnins shows that an SCL through a singularity still converges under
refinement. Each clause still needs checking against the licensed text.

Anything the module cannot stand behind raises `LinearisationError` with a named `Refusal`, never a guess.

Standard library only, with `fea_stress`, `fea_stress_study` and `mesh_quality`, which are too.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

import fea_stress as fs
import fea_stress_study as study
import mesh_quality as mq

Point = tuple[float, float, float]

# Points along the line: the default, and the fewest accepted. A C3D10 field along a line is quadratic inside
# each element but kinks where the line crosses an element face, where Simpson is no longer exact. 21 points
# (10 Simpson panels) keep several points in each element of a wall a few elements thick.
DEFAULT_POINTS = 21
MIN_POINTS = DEFAULT_POINTS
# A point is inside an element when each natural coordinate is at least -_INSIDE: an end on the surface,
# or a point on a face shared by two elements, rounds to either side of zero.
_INSIDE = 1e-9
# Newton iterations on a C3D10 map, and the step in natural coordinates that counts as converged.
_NEWTON_STEPS = 25
_NEWTON_STEP = 1e-13
# A curved C3D10 edge can bow past the box of its nodes, so each element's box grows by this share of its size.
_BOX_PAD = 0.25
# A tangent whose part normal to the line is below this share of its length is along the line.
_TANGENT_FLOOR = 1e-9


class Refusal(StrEnum):
    """Why a linearisation was refused."""

    TENSOR_MISSING = "tensor_missing"  # the result holds no stress tensor at all
    TENSOR_INCOMPLETE = "tensor_incomplete"  # a node of the mesh has no tensor
    TENSOR_NOT_FINITE = "tensor_not_finite"  # a tensor component is nan or infinite
    NODE_MISSING = "node_missing"  # a node of the mesh has no finite coordinates
    OUTSIDE_MESH = "outside_mesh"  # a point lies in no element
    UNSUPPORTED_ELEMENT = "unsupported_element"  # a point lies only near an element that is not C3D4 or C3D10
    ZERO_LENGTH = "zero_length"
    LINE_NOT_FINITE = "line_not_finite"
    TOO_FEW_POINTS = "too_few_points"
    EVEN_POINTS = "even_points"  # Simpson's rule needs an odd number of points
    TANGENT_ALONG_LINE = "tangent_along_line"  # the tangent hint has no part normal to the line


class LinearisationError(ValueError):
    """The linearisation was refused; `reason` names why, and the message gives the detail."""

    def __init__(self, reason: Refusal, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason


class SclStress(NamedTuple):
    """A symmetric stress tensor in the SCL axes, in MPa, laid out as `fea_stress.StressTensor` is.

    `t1`, `t2` and `n` are the normal stresses along t1, t2 and the line. `t1t2`, `t2n` and `nt1` are the shears.
    """

    t1: float
    t2: float
    n: float
    t1t2: float
    t2n: float
    nt1: float


@dataclass(frozen=True)
class Linearisation:
    """The stress along one SCL, split into its Annex 5-A parts. Every tensor is in the SCL axes.

    `bending` is the bending stress at `start`. At `end` it is the negative of that. Only its `t1` and `t2`
    components can be nonzero. `positions` and `stresses` give each point and its total stress, from `start`
    to `end`. Each von Mises stress is that of a linearised tensor: the membrane, the membrane plus bending at
    each end, and the total (membrane, bending and peak) at each end.
    """

    start: Point
    end: Point
    thickness: float
    t1: Point
    t2: Point
    normal: Point
    positions: tuple[Point, ...]
    stresses: tuple[SclStress, ...]
    membrane: SclStress
    bending: SclStress
    peak_start: SclStress
    peak_end: SclStress
    von_mises_membrane: float
    von_mises_membrane_bending_start: float
    von_mises_membrane_bending_end: float
    von_mises_total_start: float
    von_mises_total_end: float


# --- vectors ---------------------------------------------------------------------------------------------------

def _sub(a: Sequence[float], b: Sequence[float]) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Point, b: Point) -> Point:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _scale(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k, a[2] * k)


def _add_points(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _fmt(p: Sequence[float]) -> str:
    return "(" + ", ".join(f"{c:.6g}" for c in p) + ")"


def _solve(m: Sequence[Sequence[float]], b: Point) -> Point | None:
    """The solution of the 3 x 3 system m x = b by Cramer's rule, or None when m is singular."""
    def det(a: Sequence[Sequence[float]]) -> float:
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    d = det(m)
    if not math.isfinite(d) or d == 0.0:
        return None
    columns = [[[b[r] if c == k else m[r][c] for c in range(3)] for r in range(3)] for k in range(3)]
    return (det(columns[0]) / d, det(columns[1]) / d, det(columns[2]) / d)


# --- the line --------------------------------------------------------------------------------------------------

def _line(start: Point, end: Point, points: int) -> tuple[float, Point]:
    """The length and unit direction of the line, after the checks on the line and the point count."""
    if points < MIN_POINTS:
        raise LinearisationError(Refusal.TOO_FEW_POINTS, f"{points} points, fewer than {MIN_POINTS}")
    if points % 2 == 0:
        raise LinearisationError(Refusal.EVEN_POINTS, f"{points} points: Simpson's rule needs an odd number")
    if not all(math.isfinite(c) for c in (*start, *end)):
        raise LinearisationError(Refusal.LINE_NOT_FINITE, f"the line runs from {_fmt(start)} to {_fmt(end)}")
    length = math.dist(start, end)
    if not length > 0.0:
        raise LinearisationError(Refusal.ZERO_LENGTH, f"the line starts and ends at {_fmt(start)}")
    return length, _scale(_sub(end, start), 1.0 / length)


def _axes(normal: Point, tangent: Point | None) -> tuple[Point, Point]:
    """The tangential axes t1 and t2 of the convention in the module docstring."""
    if tangent is None:
        least = min(range(3), key=lambda k: abs(normal[k]))
        tangent = (1.0 if least == 0 else 0.0, 1.0 if least == 1 else 0.0, 1.0 if least == 2 else 0.0)
    across = _sub(tangent, _scale(normal, _dot(tangent, normal)))
    size = math.sqrt(_dot(across, across))
    if not size > _TANGENT_FLOOR * math.sqrt(_dot(tangent, tangent)):
        raise LinearisationError(Refusal.TANGENT_ALONG_LINE, f"the tangent {_fmt(tangent)} has no part normal to "
                                                            f"the line {_fmt(normal)}")
    t1 = _scale(across, 1.0 / size)
    return t1, _cross(normal, t1)


# --- the mesh --------------------------------------------------------------------------------------------------

def _check_mesh(result: fs.FrdResult, elements: Mapping[int, study.Element]) -> None:
    """Refuse a mesh node without finite coordinates, or without a complete, finite tensor."""
    if not result.stress:
        raise LinearisationError(Refusal.TENSOR_MISSING, "the result holds no stress tensor")
    for element_id in sorted(elements):
        for node in elements[element_id].nodes:
            where = f"node {node} of element {element_id}"
            point = result.coordinates.get(node)
            if point is None or not all(math.isfinite(c) for c in point):
                raise LinearisationError(Refusal.NODE_MISSING, f"{where} has no finite coordinates")
            tensor = result.stress.get(node)
            if tensor is None:
                raise LinearisationError(Refusal.TENSOR_INCOMPLETE, f"{where} has no stress tensor")
            if not all(math.isfinite(c) for c in tensor):
                raise LinearisationError(Refusal.TENSOR_NOT_FINITE, f"{where} has the stress tensor {tensor}")


@dataclass(frozen=True)
class _Cell:
    """One element near the line: its node coordinates and its box, grown by _BOX_PAD."""

    element_id: int
    element: study.Element
    xyz: tuple[Point, ...]
    low: Point
    high: Point

    @property
    def supported(self) -> bool:
        return study.ELEMENT_NODES.get(self.element.kind) == len(self.element.nodes)

    def boxes(self, p: Point) -> bool:
        return all(self.low[k] <= p[k] <= self.high[k] for k in range(3))


def _cells(result: fs.FrdResult, elements: Mapping[int, study.Element], start: Point, end: Point) -> list[_Cell]:
    """The elements whose grown box meets the box of the line, in id order."""
    line_low = tuple(min(a, b) for a, b in zip(start, end))
    line_high = tuple(max(a, b) for a, b in zip(start, end))
    cells = []
    for element_id in sorted(elements):
        element = elements[element_id]
        xyz = tuple(result.coordinates[node] for node in element.nodes)
        low = [min(p[k] for p in xyz) for k in range(3)]
        high = [max(p[k] for p in xyz) for k in range(3)]
        pad = _BOX_PAD * max(h - lo for h, lo in zip(high, low))
        low_p = (low[0] - pad, low[1] - pad, low[2] - pad)
        high_p = (high[0] + pad, high[1] + pad, high[2] + pad)
        if all(low_p[k] <= line_high[k] and line_low[k] <= high_p[k] for k in range(3)):
            cells.append(_Cell(element_id, element, xyz, low_p, high_p))
    return cells


# --- shape functions -------------------------------------------------------------------------------------------

# d(L1..L4)/d(xi, eta, zeta) with L1 = 1 - xi - eta - zeta, L2 = xi, L3 = eta and L4 = zeta (mesh_quality's).
_DL = ((-1.0, -1.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _volume_coordinates(xi: Point) -> tuple[float, float, float, float]:
    return (1.0 - xi[0] - xi[1] - xi[2], xi[0], xi[1], xi[2])


def _shape(kind: str, xi: Point) -> list[float]:
    """The shape functions at natural coordinates `xi`, in the ccx node order.

    C3D4 takes the volume coordinates L1 to L4. C3D10 takes L(2L - 1) at each corner and 4 La Lb at the
    mid-edge node of edge a-b, in the edge order of the ccx manual (`mesh_quality.EDGES`).
    """
    lv = _volume_coordinates(xi)
    if kind == "C3D4":
        return list(lv)
    return [v * (2.0 * v - 1.0) for v in lv] + [4.0 * lv[a] * lv[b] for a, b in mq.EDGES]


def _shape_gradients(kind: str, xi: Point) -> list[Point]:
    """The derivatives of `_shape` with respect to (xi, eta, zeta)."""
    if kind == "C3D4":
        return list(_DL)
    lv = _volume_coordinates(xi)
    corners = [_scale(_DL[i], 4.0 * lv[i] - 1.0) for i in range(4)]
    middles = [_add_points(_scale(_DL[b], 4.0 * lv[a]), _scale(_DL[a], 4.0 * lv[b])) for a, b in mq.EDGES]
    return corners + middles


def _natural(cell: _Cell, p: Point) -> Point | None:
    """The natural coordinates of `p` in `cell`, or None when its map does not invert there.

    The corners' affine map gives the start, which is exact for a C3D4 and for a straight-sided C3D10. Newton
    steps on the quadratic map then correct it for a curved or unevenly noded C3D10.
    """
    corner = cell.xyz[:4]
    affine = [[corner[j + 1][i] - corner[0][i] for j in range(3)] for i in range(3)]
    xi = _solve(affine, _sub(p, corner[0]))
    if xi is None or cell.element.kind == "C3D4":
        return xi
    for _ in range(_NEWTON_STEPS):
        weights, gradients = _shape("C3D10", xi), _shape_gradients("C3D10", xi)
        mapped = [sum(w * x[i] for w, x in zip(weights, cell.xyz)) for i in range(3)]
        jacobian = [[sum(g[j] * x[i] for g, x in zip(gradients, cell.xyz)) for j in range(3)] for i in range(3)]
        step = _solve(jacobian, _sub(p, mapped))
        if step is None:
            return None
        xi = (xi[0] + step[0], xi[1] + step[1], xi[2] + step[2])
        if max(abs(s) for s in step) <= _NEWTON_STEP:
            return xi
    return None


def _locate(cells: Sequence[_Cell], p: Point) -> tuple[_Cell, list[float]] | None:
    """The supported element that holds `p` most deeply, with its shape functions there, or None."""
    best: tuple[float, _Cell, Point] | None = None
    for cell in cells:
        if not cell.supported or not cell.boxes(p):
            continue
        xi = _natural(cell, p)
        if xi is None:
            continue
        depth = min(_volume_coordinates(xi))
        if depth >= -_INSIDE and (best is None or depth > best[0]):
            best = (depth, cell, xi)
    if best is None:
        return None
    return best[1], _shape(best[1].element.kind, best[2])


def _sample(result: fs.FrdResult, cells: Sequence[_Cell], positions: Sequence[Point]) -> Iterator[fs.StressTensor]:
    """The interpolated global tensor at each position, refusing a point no supported element holds."""
    for index, p in enumerate(positions):
        found = _locate(cells, p)
        if found is None:
            where = f"point {index + 1} of {len(positions)} at {_fmt(p)}"
            others = [c for c in cells if not c.supported and c.boxes(p)]
            if others:
                raise LinearisationError(Refusal.UNSUPPORTED_ELEMENT,
                                         f"{where} lies only near element {others[0].element_id}, a "
                                         f"{others[0].element.kind} of {len(others[0].element.nodes)} nodes, "
                                         f"not a C3D4 or C3D10")
            raise LinearisationError(Refusal.OUTSIDE_MESH, f"{where} lies in no element of the mesh")
        cell, weights = found
        tensors = [result.stress[node] for node in cell.element.nodes]
        yield fs.StressTensor(*(sum(w * t[c] for w, t in zip(weights, tensors)) for c in range(6)))


# --- the split -------------------------------------------------------------------------------------------------

def _rotate(s: fs.StressTensor, axes: tuple[Point, Point, Point]) -> SclStress:
    """The tensor `s` in the axes (t1, t2, n): each component is a_i . S . a_j."""
    full = ((s.sxx, s.sxy, s.szx), (s.sxy, s.syy, s.syz), (s.szx, s.syz, s.szz))

    def component(a: Point, b: Point) -> float:
        return sum(a[i] * full[i][j] * b[j] for i in range(3) for j in range(3))
    t1, t2, n = axes
    return SclStress(component(t1, t1), component(t2, t2), component(n, n), component(t1, t2), component(t2, n),
                     component(n, t1))


def _simpson(values: Sequence[float], step: float) -> float:
    """The composite Simpson integral of equally spaced `values`, an odd number of them."""
    inner = sum((4.0 if i % 2 else 2.0) * v for i, v in enumerate(values[1:-1], start=1))
    return step / 3.0 * (values[0] + inner + values[-1])


def _von_mises(s: SclStress) -> float:
    return math.sqrt(0.5 * ((s.t1 - s.t2) ** 2 + (s.t2 - s.n) ** 2 + (s.n - s.t1) ** 2)
                     + 3.0 * (s.t1t2 ** 2 + s.t2n ** 2 + s.nt1 ** 2))


def _add(a: SclStress, b: SclStress, k: float = 1.0) -> SclStress:
    return SclStress(*(x + k * y for x, y in zip(a, b)))


def _split(stresses: Sequence[SclStress], thickness: float) -> tuple[SclStress, SclStress]:
    """The membrane stress (Eq 5-A.1) and the bending stress at the start (Eq 5-A.2, t1 and t2 only)."""
    step = thickness / (len(stresses) - 1)
    lever = [thickness / 2.0 - i * step for i in range(len(stresses))]
    membrane = SclStress(*(_simpson([s[c] for s in stresses], step) / thickness for c in range(6)))
    bent = [6.0 / thickness ** 2 * _simpson([s[c] * x for s, x in zip(stresses, lever)], step) for c in range(2)]
    return membrane, SclStress(bent[0], bent[1], 0.0, 0.0, 0.0, 0.0)


def linearise(result: fs.FrdResult, elements: Mapping[int, study.Element], start: Point, end: Point, *,
              points: int = DEFAULT_POINTS, tangent: Point | None = None) -> Linearisation:
    """Linearise the stress of `result` along the SCL from `start` to `end`.

    `elements` is the mesh, as `fea_stress_study.element_connectivity` reads it. `points` is the odd number of
    points, at least MIN_POINTS. `tangent` sets the t1 axis (see the module docstring). Raises
    `LinearisationError` when the line, the point count, the tangent, the mesh or the tensor is unusable, or
    when a point lies outside every C3D4 and C3D10 element.
    """
    thickness, normal = _line(start, end, points)
    t1, t2 = _axes(normal, tangent)
    _check_mesh(result, elements)
    along = _sub(end, start)
    inner = (_add_points(start, _scale(along, i / (points - 1))) for i in range(1, points - 1))
    positions = (start, *inner, end)
    cells = _cells(result, elements, start, end)
    stresses = tuple(_rotate(s, (t1, t2, normal)) for s in _sample(result, cells, positions))
    membrane, bending = _split(stresses, thickness)
    at_start, at_end = _add(membrane, bending), _add(membrane, bending, -1.0)
    return Linearisation(
        start=start, end=end, thickness=thickness, t1=t1, t2=t2, normal=normal,
        positions=positions, stresses=stresses,
        membrane=membrane, bending=bending,
        peak_start=_add(stresses[0], at_start, -1.0), peak_end=_add(stresses[-1], at_end, -1.0),
        von_mises_membrane=_von_mises(membrane),
        von_mises_membrane_bending_start=_von_mises(at_start), von_mises_membrane_bending_end=_von_mises(at_end),
        von_mises_total_start=_von_mises(stresses[0]), von_mises_total_end=_von_mises(stresses[-1]))
