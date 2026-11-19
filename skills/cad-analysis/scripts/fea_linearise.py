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

Anything the module cannot stand behind raises `LinearisationError` with a named `Refusal`, never a guess. That
covers an element map that does not invert at a point, and finite input whose arithmetic leaves the float range.
The two stay apart. An element map that fails to invert at a point is geometry, and another element may still
hold the point. A box span, determinant, Jacobian, mapped position or natural coordinate that is not finite is
arithmetic. That refuses the whole call as `value_out_of_range`, in any element the scan reaches.

Each call screens every element and node of the mesh and builds a box for each element, so it costs time in
proportion to the mesh. Sampling then tries, at each point, every element whose box holds that point. A few SCLs
per solve make that acceptable. A caller with many lines (#209 PR-c) may add a spatial index.

Standard library only, with `fea_stress`, `fea_stress_study` and `mesh_quality`, which are too.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, fields
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
# A 3 x 3 matrix is singular when its determinant is at most this share of its Frobenius norm cubed, the
# determinant's scale. Rounding leaves a determinant near 1e-16 of that scale where the exact one is zero.
_SINGULAR = 1e-12


class Refusal(StrEnum):
    """Why a linearisation was refused."""

    TENSOR_MISSING = "tensor_missing"  # the result holds no stress tensor at all
    TENSOR_INCOMPLETE = "tensor_incomplete"  # a node of the mesh has no tensor
    TENSOR_NOT_FINITE = "tensor_not_finite"  # a tensor component is nan or infinite
    NODE_MISSING = "node_missing"  # a node of the mesh has no finite coordinates
    OUTSIDE_MESH = "outside_mesh"  # a point lies in no element
    INVERSION_FAILED = "inversion_failed"  # no element holds a point, and an element map did not invert there
    UNSUPPORTED_ELEMENT = "unsupported_element"  # an element is not a C3D4 or C3D10 of its node count
    ZERO_LENGTH = "zero_length"
    LINE_NOT_FINITE = "line_not_finite"
    THICKNESS_OUT_OF_RANGE = "thickness_out_of_range"  # the line's length cubed is not a normal float
    VALUE_OUT_OF_RANGE = "value_out_of_range"  # an intermediate or output value overflows or is not finite
    TOO_FEW_POINTS = "too_few_points"
    EVEN_POINTS = "even_points"  # Simpson's rule needs an odd number of points
    TANGENT_ALONG_LINE = "tangent_along_line"  # the tangent hint has no part normal to the line
    TANGENT_NOT_FINITE = "tangent_not_finite"  # the tangent hint has a nan or infinite component


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


class _OutOfRange(Exception):
    """Arithmetic on finite input left the float range. The message names the value."""


def _finite(values: Sequence[float], what: str) -> None:
    """Raise `_OutOfRange` naming `what` when one of `values` is not finite."""
    if not all(math.isfinite(v) for v in values):
        raise _OutOfRange(f"{what} {_fmt(values)} is not finite")


def _solve(m: Sequence[Sequence[float]], b: Point, what: str) -> Point | None:
    """The solution of the 3 x 3 system m x = b by Cramer's rule, or None when m is singular (see _SINGULAR).

    `what` names the matrix. Raises `_OutOfRange` when m, b, the determinant, its scale or the solution is not
    finite: that is arithmetic, not a singular matrix.
    """
    def det(a: Sequence[Sequence[float]]) -> float:
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    entries = [v for row in m for v in row]
    _finite(entries, what)
    _finite(b, f"the right-hand side of {what}")
    d = det(m)
    norm = math.hypot(*entries)
    cube = norm * norm * norm
    if not math.isfinite(d) or not cube <= sys.float_info.max or (norm > 0.0 and cube < sys.float_info.min):
        raise _OutOfRange(f"the determinant {d:.6g} or the norm cubed {cube:.6g} of {what} leaves the float range")
    if not abs(d) > _SINGULAR * cube:
        return None
    columns = [[[b[r] if c == k else m[r][c] for c in range(3)] for r in range(3)] for k in range(3)]
    x = (det(columns[0]) / d, det(columns[1]) / d, det(columns[2]) / d)
    _finite(x, f"the solution of {what}")
    return x


# --- the line --------------------------------------------------------------------------------------------------

def _line(start: Point, end: Point, points: int) -> tuple[float, Point]:
    """The length and unit direction of the line, after the checks on the line and the point count.

    Eq 5-A.2 divides by the thickness squared, so the thickness cubed must be a normal float: that keeps the
    square and the cube clear of underflow and overflow.
    """
    if points < MIN_POINTS:
        raise LinearisationError(Refusal.TOO_FEW_POINTS, f"{points} points, fewer than {MIN_POINTS}")
    if points % 2 == 0:
        raise LinearisationError(Refusal.EVEN_POINTS, f"{points} points: Simpson's rule needs an odd number")
    if not all(math.isfinite(c) for c in (*start, *end)):
        raise LinearisationError(Refusal.LINE_NOT_FINITE, f"the line runs from {_fmt(start)} to {_fmt(end)}")
    length = math.dist(start, end)
    if not length > 0.0:
        raise LinearisationError(Refusal.ZERO_LENGTH, f"the line starts and ends at {_fmt(start)}")
    cube = length * length * length  # inf on overflow, where ** would raise
    if not sys.float_info.min <= cube <= sys.float_info.max:
        raise LinearisationError(Refusal.THICKNESS_OUT_OF_RANGE, f"the line is {length:.6g} long, so its cube "
                                                                f"leaves the range of a normal float")
    return length, _scale(_sub(end, start), 1.0 / length)


def _axes(normal: Point, tangent: Point | None) -> tuple[Point, Point]:
    """The tangential axes t1 and t2 of the convention in the module docstring."""
    if tangent is None:
        least = min(range(3), key=lambda k: abs(normal[k]))
        tangent = (1.0 if least == 0 else 0.0, 1.0 if least == 1 else 0.0, 1.0 if least == 2 else 0.0)
    if not all(math.isfinite(c) for c in tangent):
        raise LinearisationError(Refusal.TANGENT_NOT_FINITE, f"the tangent {_fmt(tangent)} is not finite")
    largest = max(abs(c) for c in tangent)
    hint = (tangent[0] / largest, tangent[1] / largest, tangent[2] / largest) if largest > 0.0 else tangent
    across = _sub(hint, _scale(normal, _dot(hint, normal)))
    size = math.sqrt(_dot(across, across))
    if not size > _TANGENT_FLOOR * math.sqrt(_dot(hint, hint)):
        raise LinearisationError(Refusal.TANGENT_ALONG_LINE, f"the tangent {_fmt(tangent)} has no part normal to "
                                                            f"the line {_fmt(normal)}")
    t1 = _scale(across, 1.0 / size)
    return t1, _cross(normal, t1)


# --- the mesh --------------------------------------------------------------------------------------------------

def _check_mesh(result: fs.FrdResult, elements: Mapping[int, study.Element]) -> None:
    """Refuse an element that is not a C3D4 or C3D10 of its node count, wherever it lies, and a mesh node without
    finite coordinates or without a complete, finite tensor. Every element passes before any box is built."""
    if not result.stress:
        raise LinearisationError(Refusal.TENSOR_MISSING, "the result holds no stress tensor")
    for element_id in sorted(elements):
        element = elements[element_id]
        if study.ELEMENT_NODES.get(element.kind) != len(element.nodes):
            raise LinearisationError(Refusal.UNSUPPORTED_ELEMENT, f"element {element_id} is a {element.kind[:20]} "
                                                                 f"of {len(element.nodes)} nodes, not a C3D4 of 4 "
                                                                 f"or a C3D10 of 10")
        for node in element.nodes:
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

    def boxes(self, p: Point) -> bool:
        return all(self.low[k] <= p[k] <= self.high[k] for k in range(3))


def _box(element_id: int, xyz: Sequence[Point]) -> tuple[Point, Point]:
    """The box of an element's nodes, grown by _BOX_PAD. Refuses a box whose span or growth is not finite."""
    low = [min(p[k] for p in xyz) for k in range(3)]
    high = [max(p[k] for p in xyz) for k in range(3)]
    pad = _BOX_PAD * max(h - lo for h, lo in zip(high, low))
    low_p = (low[0] - pad, low[1] - pad, low[2] - pad)
    high_p = (high[0] + pad, high[1] + pad, high[2] + pad)
    if not all(math.isfinite(v) for v in (pad, *low_p, *high_p)):
        raise LinearisationError(Refusal.VALUE_OUT_OF_RANGE, f"element {element_id} has nodes from {_fmt(low)} to "
                                                            f"{_fmt(high)}, a box span beyond the float range")
    return low_p, high_p


def _cells(result: fs.FrdResult, elements: Mapping[int, study.Element], start: Point, end: Point) -> list[_Cell]:
    """The elements whose grown box meets the box of the line, in id order. Every element's box must be finite."""
    line_low = tuple(min(a, b) for a, b in zip(start, end))
    line_high = tuple(max(a, b) for a, b in zip(start, end))
    cells = []
    for element_id in sorted(elements):
        element = elements[element_id]
        xyz = tuple(result.coordinates[node] for node in element.nodes)
        low_p, high_p = _box(element_id, xyz)
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


class _NoInverse(Exception):
    """An element map that does not invert at a point. The message says how, after the element's id."""


def _natural(cell: _Cell, p: Point) -> Point:
    """The natural coordinates of `p` in `cell`. Raises `_NoInverse` when the element's map does not invert there.

    The corners' affine map gives the start, which is exact for a C3D4 and for a straight-sided C3D10. Newton
    steps on the quadratic map then correct it for a curved or unevenly noded C3D10. A singular Jacobian, or
    no convergence within _NEWTON_STEPS, is a failure: the affine start never stands in for the inverse.
    Raises `_OutOfRange` when a determinant, a Jacobian, a mapped position or a natural coordinate is not finite.
    """
    corner = cell.xyz[:4]
    affine = [[corner[j + 1][i] - corner[0][i] for j in range(3)] for i in range(3)]
    start = _solve(affine, _sub(p, corner[0]), "the affine map")
    if start is None:
        raise _NoInverse("has corners that span no volume")
    if cell.element.kind == "C3D4":
        return start
    xi = start
    for _ in range(_NEWTON_STEPS):
        weights, gradients = _shape("C3D10", xi), _shape_gradients("C3D10", xi)
        mapped = (sum(w * x[0] for w, x in zip(weights, cell.xyz)), sum(w * x[1] for w, x in zip(weights, cell.xyz)),
                  sum(w * x[2] for w, x in zip(weights, cell.xyz)))
        _finite(mapped, f"the position mapped from {_fmt(xi)}")
        jacobian = [[sum(g[j] * x[i] for g, x in zip(gradients, cell.xyz)) for j in range(3)] for i in range(3)]
        step = _solve(jacobian, _sub(p, mapped), f"the Jacobian at {_fmt(xi)}")
        if step is None:
            raise _NoInverse(f"has a singular Jacobian at the natural coordinates {_fmt(xi)}")
        xi = (xi[0] + step[0], xi[1] + step[1], xi[2] + step[2])
        _finite(xi, "the natural coordinates")
        if max(abs(s) for s in step) <= _NEWTON_STEP:
            return xi
    raise _NoInverse(f"did not converge in {_NEWTON_STEPS} Newton steps")


def _locate(cells: Sequence[_Cell], p: Point, where: str) -> tuple[_Cell, list[float]]:
    """The element that holds `p` most deeply, with its shape functions there.

    Raises VALUE_OUT_OF_RANGE as soon as any element whose box holds `p` leaves the float range, even when
    another element holds `p`. Raises INVERSION_FAILED when no element holds `p` and an element whose box does
    failed to invert there, and OUTSIDE_MESH when no element holds `p` and none failed.
    """
    best: tuple[float, _Cell, Point] | None = None
    failure: str | None = None
    for cell in cells:
        if not cell.boxes(p):
            continue
        try:
            xi = _natural(cell, p)
        except _OutOfRange as exc:
            raise LinearisationError(Refusal.VALUE_OUT_OF_RANGE, f"{where}: in element {cell.element_id}, {exc}") from None
        except _NoInverse as exc:
            failure = failure or f"element {cell.element_id} {exc}"
            continue
        depth = min(_volume_coordinates(xi))
        if depth >= -_INSIDE and (best is None or depth > best[0]):
            best = (depth, cell, xi)
    if best is not None:
        return best[1], _shape(best[1].element.kind, best[2])
    if failure is not None:
        raise LinearisationError(Refusal.INVERSION_FAILED, f"{where} lies in no element that inverts there: {failure}")
    raise LinearisationError(Refusal.OUTSIDE_MESH, f"{where} lies in no element of the mesh")


def _sample(result: fs.FrdResult, cells: Sequence[_Cell], positions: Sequence[Point]) -> Iterator[fs.StressTensor]:
    """The interpolated global tensor at each position."""
    for index, p in enumerate(positions):
        cell, weights = _locate(cells, p, f"point {index + 1} of {len(positions)} at {_fmt(p)}")
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


def _assemble(result: fs.FrdResult, elements: Mapping[int, study.Element], start: Point, end: Point,
              points: int, thickness: float, axes: tuple[Point, Point, Point]) -> Linearisation:
    """The linearisation of a checked line and mesh. `linearise` refuses an OverflowError or ZeroDivisionError."""
    along = _sub(end, start)
    inner = (_add_points(start, _scale(along, i / (points - 1))) for i in range(1, points - 1))
    positions = (start, *inner, end)
    cells = _cells(result, elements, start, end)
    stresses = tuple(_rotate(s, axes) for s in _sample(result, cells, positions))
    membrane, bending = _split(stresses, thickness)
    at_start, at_end = _add(membrane, bending), _add(membrane, bending, -1.0)
    t1, t2, normal = axes
    return Linearisation(
        start=start, end=end, thickness=thickness, t1=t1, t2=t2, normal=normal,
        positions=positions, stresses=stresses,
        membrane=membrane, bending=bending,
        peak_start=_add(stresses[0], at_start, -1.0), peak_end=_add(stresses[-1], at_end, -1.0),
        von_mises_membrane=_von_mises(membrane),
        von_mises_membrane_bending_start=_von_mises(at_start), von_mises_membrane_bending_end=_von_mises(at_end),
        von_mises_total_start=_von_mises(stresses[0]), von_mises_total_end=_von_mises(stresses[-1]))


def _floats(value: object) -> Iterator[float]:
    """Every float in a field of `Linearisation`: a float, a point, a tensor or a tuple of them."""
    if isinstance(value, tuple):
        for item in value:
            yield from _floats(item)
    elif isinstance(value, float | int):
        yield float(value)


def _check_finite(linearisation: Linearisation) -> None:
    """Refuse a linearisation that holds a value that is not finite: one beyond the float range, or its nan."""
    for item in fields(linearisation):
        if not all(math.isfinite(v) for v in _floats(getattr(linearisation, item.name))):
            raise LinearisationError(Refusal.VALUE_OUT_OF_RANGE, f"{item.name} holds a value beyond the float range")


def linearise(result: fs.FrdResult, elements: Mapping[int, study.Element], start: Point, end: Point, *,
              points: int = DEFAULT_POINTS, tangent: Point | None = None) -> Linearisation:
    """Linearise the stress of `result` along the SCL from `start` to `end`.

    `elements` is the mesh, as `fea_stress_study.element_connectivity` reads it. `points` is the odd number of
    points, at least MIN_POINTS. `tangent` sets the t1 axis (see the module docstring). Raises
    `LinearisationError` when the line, the point count, the tangent, the mesh or the tensor is unusable, when a
    point lies outside every C3D4 and C3D10 element or where its element's map does not invert, and when the
    arithmetic leaves the float range: an OverflowError or ZeroDivisionError, or a value that is not finite.
    """
    thickness, normal = _line(start, end, points)
    t1, t2 = _axes(normal, tangent)
    _check_mesh(result, elements)
    try:
        linearisation = _assemble(result, elements, start, end, points, thickness, (t1, t2, normal))
    except (OverflowError, ZeroDivisionError) as exc:
        raise LinearisationError(Refusal.VALUE_OUT_OF_RANGE, f"the arithmetic left the float range ({exc})") from None
    _check_finite(linearisation)
    return linearisation
