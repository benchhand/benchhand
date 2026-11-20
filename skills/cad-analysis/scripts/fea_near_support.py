# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Strength near a support or a load: the stress classification lines of #209 and the cross-check that gates them.

A `settled_away_from_support` study cannot read the stress inside `K_EXCLUSION * h_coarse` of a fixed or load face,
so it gives no strength value (fea_stress). This module gives one only where the stress can be linearised and the
linearisation proved against the applied loads:

* Every fixed face and every `*CLOAD` face is a face. Load faces count as fixed faces: the stress next to a load
  is as unreadable as next to a support. A face is a flat set of nodes on one global axis plane at an end of the
  part, else the study abstains (`support_not_axis_planar`, `face_not_on_boundary`).
* The zone is `max(1 * t_local, 3 * h_coarse)` deep (`zone_depth`), where `t_local` is the face's smaller in-plane
  extent. A section parallel to the face, one zone depth inside the part, holds the lines. The stress on the face
  itself is not used: nodal stress there is singular, and a section force there fails the cross-check (the 4.5 mm
  cantilever fixture reads 3.3 times the load at x = 0).
* The line axis is the smaller in-plane extent of the section (the lower axis on a tie). `STRIPS` parallel lines at
  the mid-points of equal strips across the other extent stand for the section. `fea_linearise` linearises each
  one with the section normal as its tangent, so t1 is the axial direction. A line that leaves the mesh, or any
  other refusal of `fea_linearise`, abstains with the reason `scl_<refusal>`.
* The strength stress is the largest von Mises stress of any linearised membrane, or membrane plus bending, at
  either end of any line. There is no 1.5 shape-factor credit on the bending: the check is first yield. The peak
  stress (membrane, bending and peak at the line ends) is reported and never gives a pass.

The cross-check gates each section. The free body is the side of the section that holds no fixed node. Fixed nodes on
both sides, or more than one fixed face, abstain as `statically_indeterminate`: the reactions are not statics' to
give. The section force is the sum, over the lines, of the line's width times its thickness times the membrane
traction on the section. The section moment about the section centroid adds each line's offset from it, and the
axial bending couple, -sigma_b t^2 / 6 per unit width along t2 (Annex 5-A Eq 5-A.2 gives the bending). Both must
balance the applied nodal force and moment on the free body, within `TOLERANCE` (5%) of the sum of the nodal force
magnitudes for the force, and for the moment of the larger of the sum of the nodal moment magnitudes and a reference
F x L (the force scale times the section's longest in-plane extent), or the study abstains (`cross_check_failed`).
The reference keeps the moment check alive for a load with no moment about the section, such as an axial load at the
centre, whose nodal moment scale is zero; a residual is never taken as zero for want of a scale, and one that is not
a number fails. A free body with no force abstains first (`no_applied_load`), so the force scale is never zero.
Annex 5-A has no torsion, and a small torque hides in the aggregate residual, so a torque about the section normal
above `TORSION_FLOOR` of F x L abstains on its own (`torsion_on_section`), whatever the residual. The same floor
screens the torque the section's own stresses produce about the normal: the moment of the full traction along the
lines, which holds the first moment of the shear across the thickness that the Annex split discards, as well as the
membrane shear's arm. A shear gradient can twist a section while the force balances and the bending moments pass
the aggregate residual. The other resultants are all in the check: the three force components, the moment about the
two in-plane axes (the membrane arms and the bending couple) and, here, the torque. Each residual is compared on
its own, so a nan fails; the norms use math.hypot so a 3e153 N load does not overflow; a node, mesh size or load that
is not finite abstains first (`mesh_not_finite`, `load_not_finite`).

Neither ASME VIII-2 Annex 5-A nor EN 13445-3 gives a number for these thresholds. The user chose the 5% of
`TOLERANCE` and the 0.1% of `TORSION_FLOOR` on 20 Nov 2026 (decision U-2 on #209) as engineering judgment, not as
provisional values awaiting a source. A section that misses either gate abstains (`unknown`) and never gives a pass or
a fail. With the gate passed, the stress error is of the same order as the residual, which is small against a design
factor, and the check is first yield with no 1.5 bending credit. Annex 5-A has no torsion term, so refusing on a torque
about the section normal is conservative.
The research behind this read Mackenzie PVP2017 (strathprints 60871) on 5-A.4.1.2, the O'Donnell and Becht summaries
(placing the classification lines is judgment) and the EN 13445 background to part 3. It did not read the full texts
of ASME VIII-2, of EN 13445-3 Annex C or of the NAFEMS guides.

The 5% was set against the 4.5 mm cantilever fixture (test_fea_near_support), and that fixture does not clear it: at
its zone depth the sections show force residuals of 5.2% and 6.1% and moment residuals under 0.5%, and at other coarse
sizes (2 to 6 mm) the force residual reaches 18%. ccx smooths nodal stress, and the shear force suffers most. A real
mesh therefore abstains on the force gate more often than on the moment gate.

The clause numbers of Annex 5-A come from secondary sources (Kezar Engineering's summary, Mackenzie PVP2017 and
Kalnins PVP2008); see `fea_linearise`. Each still needs checking against the licensed text.

Standard library only, with `fea_linearise`, `fea_stress` and `fea_stress_study`, which are too.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fea_linearise as fl
import fea_stress as fs
import fea_stress_study as study

Vec = tuple[float, float, float]

# The zone is this many local thicknesses deep, or K_EXCLUSION coarse mesh sizes, whichever is more (decision, #209).
T_LOCAL_FACTOR = 1.0
# Lines across a section's width, one per equal strip.
STRIPS = 9
# Largest section force and moment residual, as a share of the sum of the nodal force and moment magnitudes.
# Engineering judgment, chosen by the user on 20 Nov 2026 (U-2, #209): no standard gives a number. A miss abstains,
# and a pass leaves a stress error of the order of the residual, small against a design factor. See the module
# docstring.
TOLERANCE = 0.05
# A torque about the section normal above this share of F x L abstains. F is the sum of the nodal force magnitudes on
# the free body and L the section's longest in-plane extent. Engineering judgment, chosen by the user on 20 Nov 2026
# like TOLERANCE: Annex 5-A has no torsion term, so refusing is conservative. 0.1% of F x L is 0.5 N mm for a 50 N load
# on a 10 mm section, a 0.01 mm offset of the load from the section centre.
TORSION_FLOOR = 1e-3
# A coordinate is on a plane, or a face is flat, when it lies within this share of the part's largest extent.
_FLAT = 1e-6

STATICALLY_INDETERMINATE = "statically_indeterminate"
CROSS_CHECK_FAILED = "cross_check_failed"
TORSION_ON_SECTION = "torsion_on_section"
SUPPORT_NOT_AXIS_PLANAR = "support_not_axis_planar"
FACE_NOT_ON_BOUNDARY = "face_not_on_boundary"
LOAD_NODES_OVERLAP = "load_nodes_overlap"
LOAD_NOT_FINITE = "load_not_finite"
MESH_NOT_FINITE = "mesh_not_finite"
NO_APPLIED_LOAD = "no_applied_load"
SECTION_OUTSIDE_PART = "section_outside_part"
SECTION_ON_SUPPORT = "section_on_support"
BOUNDARY_NODE_MISSING = "boundary_node_missing"
ELEMENTS_UNREADABLE = "elements_unreadable"
BOUNDARY_UNREADABLE = "boundary_unreadable"
RESULTS_UNREADABLE = "results_unreadable"
LINEARISED = "linearised"
UNKNOWN = "unknown"


class _Abstain(Exception):
    """The study cannot stand behind a figure: `reason` names why and `detail` gives the numbers."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Section:
    """One linearised section: where it is, the stress it gives and how well it balances the applied loads."""

    face: str  # "fixed" or "load"
    axis: str  # the section normal, x, y or z
    face_mm: float
    section_mm: float
    thickness_mm: float
    sigma_mpa: float  # the largest membrane or membrane-plus-bending von Mises stress of its lines
    peak_mpa: float  # the largest total (membrane, bending and peak) von Mises stress at a line end
    force_residual: float  # share of the force scale
    moment_residual: float  # share of the moment scale

    def as_fields(self) -> dict[str, Any]:
        return {"face": self.face, "axis": self.axis, "face_mm": self.face_mm, "section_mm": self.section_mm,
                "thickness_mm": self.thickness_mm, "sigma_mpa": self.sigma_mpa, "peak_mpa": self.peak_mpa,
                "force_residual": self.force_residual, "moment_residual": self.moment_residual}


@dataclass(frozen=True)
class NearSupport:
    """The outcome: `linearised` with the strength stress `sigma_mpa`, or `unknown` with the named reasons."""

    status: str
    sigma_mpa: float | None
    reasons: tuple[str, ...]
    message: str
    sections: tuple[Section, ...]

    def as_fields(self) -> dict[str, Any]:
        return {"outcome": self.status, "sigma_mpa": self.sigma_mpa, "reasons": list(self.reasons),
                "message": self.message, "sections": [s.as_fields() for s in self.sections],
                "tolerance": TOLERANCE}


def zone_depth(t_local: float, h_coarse: float) -> float:
    """How far inside the part a face's section lies: the larger of one local thickness and K_EXCLUSION coarse sizes."""
    return max(T_LOCAL_FACTOR * t_local, fs.K_EXCLUSION * h_coarse)


# --- vectors ---------------------------------------------------------------------------------------------------

def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec) -> float:
    """The length of `a`; math.hypot does not overflow in the squares, so 3e153 N stays finite."""
    return math.hypot(a[0], a[1], a[2])


ZERO: Vec = (0.0, 0.0, 0.0)
AXES = "xyz"


def _unit(axis: int, sign: float) -> Vec:
    return (sign if axis == 0 else 0.0, sign if axis == 1 else 0.0, sign if axis == 2 else 0.0)


def _at(axis: int, along: float, other: dict[int, float]) -> Vec:
    """A point at `along` on `axis` and the coordinates `other` on the others."""
    point = [0.0, 0.0, 0.0]
    point[axis] = along
    for k, value in other.items():
        point[k] = value
    return (point[0], point[1], point[2])


# --- the case --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Face:
    kind: str
    nodes: frozenset[int]
    axis: int
    value: float


@dataclass(frozen=True)
class _Case:
    result: fs.FrdResult
    elements: Mapping[int, study.Element]
    fixed: frozenset[int]
    forces: Mapping[int, Vec]
    h_coarse: float
    h_mesh: float
    low: Vec  # the part's bounding box
    high: Vec

    @property
    def eps(self) -> float:
        return _FLAT * max(h - lo for h, lo in zip(self.high, self.low))


def _extent(points: Sequence[Vec]) -> tuple[Vec, Vec]:
    return ((min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
            (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)))


def _merged_forces(loads: Sequence[Mapping[int, Vec]]) -> dict[int, Vec]:
    """The nodal forces of every load face. A node in two faces abstains: ccx may overwrite rather than add."""
    forces: dict[int, Vec] = {}
    for block in loads:
        for node, force in block.items():
            if node in forces:
                raise _Abstain(LOAD_NODES_OVERLAP, f"node {node} carries a load in more than one *CLOAD face")
            if not all(math.isfinite(c) for c in force):
                raise _Abstain(LOAD_NOT_FINITE, f"node {node} carries a force that is not finite")
            forces[node] = force
    return forces


def _components(fixed: Collection[int], elements: Mapping[int, study.Element]) -> list[frozenset[int]]:
    """The fixed nodes grouped by the elements that join them."""
    parent = {n: n for n in fixed}

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for element in elements.values():
        joined = [find(n) for n in element.nodes if n in parent]
        for other in joined[1:]:
            parent[find(other)] = joined[0]
    groups: dict[int, set[int]] = {}
    for n in parent:
        groups.setdefault(find(n), set()).add(n)
    return [frozenset(g) for g in groups.values()]


def _flat_face(kind: str, nodes: frozenset[int], case: _Case) -> _Face:
    """The face as a plane on one global axis, or an abstention when its nodes are not flat on exactly one."""
    missing = [n for n in nodes if n not in case.result.coordinates]
    if missing:
        raise _Abstain(BOUNDARY_NODE_MISSING, f"the {kind} face names node {min(missing)}, which the results lack")
    low, high = _extent([case.result.coordinates[n] for n in nodes])
    flat = [k for k in range(3) if high[k] - low[k] <= case.eps]
    if len(flat) != 1:
        raise _Abstain(SUPPORT_NOT_AXIS_PLANAR, f"the {kind} face is not a plane normal to one global axis "
                                               f"(extent {_fmt(_sub(high, low))} mm)")
    return _Face(kind, nodes, flat[0], low[flat[0]])


def _faces(case: _Case, loads: Sequence[Mapping[int, Vec]]) -> list[_Face]:
    parts = _components(case.fixed, case.elements)
    if len(parts) > 1:
        raise _Abstain(STATICALLY_INDETERMINATE, f"the part is fixed on {len(parts)} separate faces: a body held on "
                                                 "more than one face has reactions that statics cannot give")
    faces = [_flat_face("fixed", part, case) for part in parts]
    return faces + [_flat_face("load", frozenset(block), case) for block in loads]


def _fmt(p: Sequence[float]) -> str:
    return "(" + ", ".join(f"{c:.6g}" for c in p) + ")"


# --- one section -----------------------------------------------------------------------------------------------

def _inward(face: _Face, case: _Case) -> float:
    """+1 when the part lies above the face along its axis, -1 when below; a face inside the part abstains."""
    if abs(face.value - case.low[face.axis]) <= case.eps:
        return 1.0
    if abs(face.value - case.high[face.axis]) <= case.eps:
        return -1.0
    raise _Abstain(FACE_NOT_ON_BOUNDARY, f"the {face.kind} face at {AXES[face.axis]} = {face.value:.6g} mm lies "
                                         "inside the part, not at an end of it")


def _side(case: _Case, axis: int, plane: float, nodes: Collection[int]) -> tuple[bool, bool]:
    """Whether any of `nodes` lies below and whether any lies above the plane; a node on it abstains."""
    below = above = False
    for n in nodes:
        offset = case.result.coordinates[n][axis] - plane
        if abs(offset) <= case.eps:
            raise _Abstain(SECTION_ON_SUPPORT, f"node {n} lies on the section plane {AXES[axis]} = {plane:.6g} mm")
        below, above = below or offset < 0, above or offset > 0
    return below, above


@dataclass(frozen=True)
class _Applied:
    force: Vec
    moment: Vec
    force_scale: float
    moment_scale: float


def _applied(case: _Case, axis: int, plane: float, body: float, centre: Vec) -> _Applied:
    """The nodal forces on the free body (the side of the plane where `body` is +1 above or -1 below), their sum,
    their moment about `centre` and the sums of their magnitudes."""
    force, moment, f_scale, m_scale = ZERO, ZERO, 0.0, 0.0
    for node, f in case.forces.items():
        if (case.result.coordinates[node][axis] - plane) * body <= 0:
            continue
        arm = _sub(case.result.coordinates[node], centre)
        force, moment = _add(force, f), _add(moment, _cross(arm, f))
        f_scale, m_scale = f_scale + _norm(f), m_scale + _norm(_cross(arm, f))
    return _Applied(force, moment, f_scale, m_scale)


def _free_body(case: _Case, axis: int, plane: float) -> float:
    """+1 when the free body is the side above the plane, -1 when below: the side that holds no fixed node."""
    below, above = _side(case, axis, plane, case.fixed)
    if below and above:
        raise _Abstain(STATICALLY_INDETERMINATE, f"fixed nodes lie on both sides of the section at "
                                                 f"{AXES[axis]} = {plane:.6g} mm")
    return 1.0 if below else -1.0


@dataclass(frozen=True)
class _Lines:
    """What the lines of one section add up to."""

    force: Vec
    moment: Vec
    torque: float  # about the section normal, from the full stress of the lines
    sigma: float
    peak: float


def _simpson(values: Sequence[float], step: float) -> float:
    """The composite Simpson integral of equally spaced `values` (an odd count, as fea_linearise needs)."""
    odd, even = sum(values[1:-1:2]), sum(values[2:-1:2])
    return step / 3.0 * (values[0] + values[-1] + 4.0 * odd + 2.0 * even)


def _line_torque(lin: fl.Linearisation, axis: int, centre: Vec) -> float:
    """The torque about the section normal (global `axis`) of the full stress along one line, per unit width.

    This is the moment of the total traction, so it holds what the Annex 5-A split discards: the first moment of
    the shear across the thickness, as well as the membrane shear's arm about `centre`."""
    density = []
    for position, stress in zip(lin.positions, lin.stresses):
        traction = _add(_add(_scale(lin.t1, stress.t1), _scale(lin.t2, stress.t1t2)), _scale(lin.normal, stress.nt1))
        density.append(_cross(_sub(position, centre), traction)[axis])
    return _simpson(density, lin.thickness / (len(density) - 1))


def _lines(case: _Case, face: _Face, plane: float, normal: Vec, centre: Vec, box: tuple[Vec, Vec]) -> _Lines:
    """Linearise the STRIPS lines of the section and sum their force, moment about `centre` and stresses."""
    low, high = box
    plane_axes = [k for k in range(3) if k != face.axis]
    wall, across = sorted(plane_axes, key=lambda k: (high[k] - low[k], k))
    thickness, width = high[wall] - low[wall], high[across] - low[across]
    force, moment, torque, sigma, peak = ZERO, ZERO, 0.0, 0.0, 0.0
    for strip in range(STRIPS):
        offset = low[across] + (strip + 0.5) * width / STRIPS
        start = _at(face.axis, plane, {wall: low[wall], across: offset})
        end = _at(face.axis, plane, {wall: high[wall], across: offset})
        try:
            lin = fl.linearise(case.result, case.elements, start, end, tangent=normal)
        except fl.LinearisationError as exc:
            raise _Abstain(f"scl_{exc.reason.value}", f"{AXES[face.axis]} = {plane:.6g} mm, line {_fmt(start)} to "
                                                      f"{_fmt(end)}: {exc}") from None
        m = lin.membrane
        traction = _add(_add(_scale(lin.t1, m.t1), _scale(lin.t2, m.t1t2)), _scale(lin.normal, m.nt1))
        piece = _scale(traction, thickness * width / STRIPS)
        mid = _scale(_add(start, end), 0.5)
        couple = _scale(lin.t2, -lin.bending.t1 * thickness ** 2 / 6.0 * width / STRIPS)
        force, moment = _add(force, piece), _add(moment, _add(_cross(_sub(mid, centre), piece), couple))
        torque += _line_torque(lin, face.axis, centre) * width / STRIPS
        sigma = max(sigma, lin.von_mises_membrane, lin.von_mises_membrane_bending_start,
                    lin.von_mises_membrane_bending_end)
        peak = max(peak, lin.von_mises_total_start, lin.von_mises_total_end)
    return _Lines(force, moment, torque, sigma, peak)


def _check_torsion(face: _Face, plane: float, applied: _Applied, reference: float) -> None:
    """Abstain when the free body carries a torque about the section normal above the floor.

    The lines integrate the membrane traction of the section, so they see a torque only through the shear they
    carry, and a torque small against the bending moments hides in the aggregate residual. The torque is judged on
    its own, against TORSION_FLOOR of `reference`, the force scale times the section's longest in-plane extent."""
    torque = abs(applied.moment[face.axis])
    if not torque <= TORSION_FLOOR * reference:
        raise _Abstain(TORSION_ON_SECTION, f"the free body beyond the section at {AXES[face.axis]} = {plane:.6g} mm "
                                           f"carries a torque {torque:.3g} N mm about the section normal, over the floor "
                                           f"{TORSION_FLOOR * reference:.3g} N mm ({TORSION_FLOOR:g} of F x L = "
                                           f"{reference:.3g} N mm): Annex 5-A linearisation has no torsion")


def _check_section_torsion(face: _Face, plane: float, lines: _Lines, reference: float) -> None:
    """Abstain when the stresses of the section carry a torque about its normal above the floor, whatever the applied
    loads say. A shear gradient across the section twists it while the force balances and the bending moments stay
    inside the aggregate residual; the torque the lines integrate is judged against the same floor as the applied one."""
    torque = abs(lines.torque)
    if not torque <= TORSION_FLOOR * reference:
        raise _Abstain(TORSION_ON_SECTION, f"the section stresses at {AXES[face.axis]} = {plane:.6g} mm carry a torque "
                                           f"{torque:.3g} N mm about the section normal, over the floor "
                                           f"{TORSION_FLOOR * reference:.3g} N mm ({TORSION_FLOOR:g} of F x L = "
                                           f"{reference:.3g} N mm): Annex 5-A linearisation has no torsion")


def _residuals(lines: _Lines, applied: _Applied, reference: float) -> tuple[float, float]:
    """The force and moment residuals as shares of their scales. The force scale is positive here, as a free body
    with none abstains first. The moment scale is the larger of the nodal moment magnitudes and `reference`, so a
    load with no moment about the section centre (a zero nodal scale) still has its residual measured."""
    force = _norm(_add(lines.force, applied.force)) / applied.force_scale
    moment = _norm(_add(lines.moment, applied.moment)) / max(applied.moment_scale, reference)
    return force, moment


def _section(case: _Case, face: _Face) -> Section:
    sign = _inward(face, case)
    low, high = _extent([case.result.coordinates[n] for n in face.nodes])
    thickness = min(high[k] - low[k] for k in range(3) if k != face.axis)
    plane = face.value + sign * zone_depth(thickness, case.h_coarse)
    where = f"the {face.kind} face at {AXES[face.axis]} = {face.value:.6g} mm"
    if not case.low[face.axis] + case.eps < plane < case.high[face.axis] - case.eps:
        raise _Abstain(SECTION_OUTSIDE_PART, f"the section for {where} would lie at {AXES[face.axis]} = {plane:.6g} "
                                             f"mm, outside the part ({case.low[face.axis]:.6g} to "
                                             f"{case.high[face.axis]:.6g} mm)")
    slab = [p for p in case.result.coordinates.values() if abs(p[face.axis] - plane) <= case.h_mesh]
    if not slab:
        raise _Abstain(SECTION_OUTSIDE_PART, f"no node lies within {case.h_mesh:g} mm of the section for {where}")
    box = _extent(slab)
    centre = _at(face.axis, plane, {k: (box[0][k] + box[1][k]) / 2 for k in range(3) if k != face.axis})
    body = _free_body(case, face.axis, plane)
    applied = _applied(case, face.axis, plane, body, centre)
    if applied.force_scale <= 0:
        raise _Abstain(NO_APPLIED_LOAD, f"no force acts on the free body beyond the section for {where}")
    if not all(math.isfinite(v) for v in (applied.force_scale, applied.moment_scale)):
        raise _Abstain(LOAD_NOT_FINITE, f"the applied loads beyond the section for {where} overflow the float range")
    reference = applied.force_scale * max(box[1][k] - box[0][k] for k in range(3) if k != face.axis)
    _check_torsion(face, plane, applied, reference)
    lines = _lines(case, face, plane, _unit(face.axis, -body), centre, box)
    f_res, m_res = _residuals(lines, applied, reference)
    # Each residual on its own: max() returns its first argument when the second is nan, which would drop it.
    if not (f_res <= TOLERANCE and m_res <= TOLERANCE):  # false for a nan residual
        raise _Abstain(CROSS_CHECK_FAILED, f"{where}, section at {AXES[face.axis]} = {plane:.6g} mm: force residual "
                                           f"{100 * f_res:.1f}% (limit {100 * TOLERANCE:g}%), moment residual "
                                           f"{100 * m_res:.1f}% (limit {100 * TOLERANCE:g}%): the integrated "
                                           "section force and moment do not match the applied loads")
    _check_section_torsion(face, plane, lines, reference)
    wall_extent = min(box[1][k] - box[0][k] for k in range(3) if k != face.axis)
    return Section(face.kind, AXES[face.axis], face.value, plane, wall_extent, lines.sigma, lines.peak, f_res, m_res)


# --- the entry points ------------------------------------------------------------------------------------------

def _unknown(reason: str, detail: str, sections: tuple[Section, ...] = ()) -> NearSupport:
    return NearSupport(UNKNOWN, None, (reason,), f"UNKNOWN ({reason}): {detail}", sections)


def assess(result: fs.FrdResult, elements: Mapping[int, study.Element], *, fixed: Collection[int],
           loads: Sequence[Mapping[int, Vec]], h_coarse: float, h_mesh: float) -> NearSupport:
    """The strength stress near the faces of one solve, or the reason it cannot be given.

    `loads` holds one nodal force map per `*CLOAD` face. `h_coarse` sets the zone and `h_mesh` is the size of the
    mesh the lines run through. Nothing here raises for a mesh or a result it cannot use: that is `unknown`.
    """
    try:
        if not result.coordinates:
            raise _Abstain(RESULTS_UNREADABLE, "the results hold no node")
        # min() and max() over a nan are order-dependent, so every number that feeds a comparison is checked first.
        bad = next((n for n, p in result.coordinates.items() if not all(math.isfinite(c) for c in p)), None)
        if bad is not None:
            raise _Abstain(MESH_NOT_FINITE, f"node {bad} has a coordinate that is not finite")
        if not (math.isfinite(h_coarse) and math.isfinite(h_mesh) and h_coarse > 0 and h_mesh > 0):
            raise _Abstain(MESH_NOT_FINITE, f"the mesh sizes {h_coarse:g} and {h_mesh:g} mm are not finite and positive")
        low, high = _extent(list(result.coordinates.values()))
        case = _Case(result, elements, frozenset(fixed), _merged_forces(loads), h_coarse, h_mesh, low, high)
        sections = tuple(_section(case, face) for face in _faces(case, loads))
    except _Abstain as exc:
        return _unknown(exc.reason, exc.detail)
    sigma = max(s.sigma_mpa for s in sections)
    detail = "; ".join(f"{s.face} face section at {s.axis} = {s.section_mm:.6g} mm: {s.sigma_mpa:.4g} MPa"
                       for s in sections)
    return NearSupport(LINEARISED, sigma, (), f"linearised, no 1.5 shape factor: {detail}", sections)


def near_support_fields(run_dir: Path, h_coarse: float, h_fine: float) -> dict[str, Any]:
    """The `near_support` result field of a `settled_away_from_support` study, from the fine solve's run directory.

    Never raises: a file it cannot read is `unknown` with the reason that names it."""
    try:
        result = fs.read_frd((run_dir / study.FRD).read_text(encoding="utf-8"))
        inp = (run_dir / study.INP).read_text(encoding="utf-8")
    except (fs.FrdError, OSError, UnicodeDecodeError) as exc:
        return _unknown(RESULTS_UNREADABLE, f"fine mesh: {exc}").as_fields()
    try:
        fixed, _ = study.boundary_nodes(inp)
        loads = study.load_blocks(inp)
    except study.BoundaryNodesError as exc:
        return _unknown(BOUNDARY_UNREADABLE, f"fine mesh: {exc}").as_fields()
    try:
        elements = study.element_connectivity(inp)
    except study.ConnectivityError as exc:
        return _unknown(ELEMENTS_UNREADABLE, f"fine mesh: {exc}").as_fields()
    return assess(result, elements, fixed=fixed, loads=loads, h_coarse=h_coarse, h_mesh=h_fine).as_fields()
