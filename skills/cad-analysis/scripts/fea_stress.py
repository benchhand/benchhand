# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Two-mesh stress study: report a peak von Mises stress only when it has settled between two meshes.

A peak stress at a fixed support or a load patch is a singularity: it keeps growing as the mesh is
refined, so no single mesh gives it a trustworthy value. The study therefore compares two solves of
the same part, a coarse one and a fine one (`h_coarse`, `h_fine`), over the region away from every
fixed face and load face:

* The exclusion distance is `K_EXCLUSION * h_coarse`, the same distance on both meshes.
* `sigma_kept` is the peak von Mises over the nodes farther than that from every fixed and load node.
* Convergence needs the kept peaks to differ by at most `SETTLE_TOLERANCE` of the fine one and the
  maximum displacement to change by at most `SETTLE_TOLERANCE`. For an interior peak the hot spot must
  also have moved by at most `h_coarse`; a peak on the exclusion boundary follows the cutoff, so its
  position is not compared.
* An interior peak that converges is `settled` and strength-eligible; it reports the larger `sigma_kept`.
* A peak within `h_coarse` of the cutoff that converges is `settled_away_from_support`: the stress rises
  toward the support, so `sigma_kept` is information only and the result is not strength-eligible.
* Otherwise the result is UNKNOWN and carries no number. The reason names both mesh sizes, both peaks,
  the change, the hot spot, the nearest boundary-condition face and, for the node-count floor, the kept
  share and count.

Sharp re-entrant corners are not excluded: they are real geometry, and a corner peak that does not
settle fails the comparison and comes out UNKNOWN. Parts whose peak is governed by a support or a
load stay UNKNOWN here; the route to a value there is the linearisation follow-up, issue #209.

Standard library only; it reads CalculiX `.frd` results and takes no FreeCAD or solver dependency.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import product
from typing import Any

# Exclusion distance in units of the coarse mesh size. Provisional: chosen against the fast-bias campaign
# (fb-2f0b457-r1 away.json), where 3 x h cleared the fixed-face singularity on the cantilever pair.
K_EXCLUSION = 3
# Largest relative change in kept peak stress, and in maximum displacement, between the two meshes.
SETTLE_TOLERANCE = 0.02
# Floor on the kept region, so that the peak is stable across the two meshes rather than set by the
# placement of a few nodes. The share applies to both meshes: a mesh that keeps under 10% of its nodes is
# mostly boundary band. The node count applies to the fine mesh only: the coarse mesh is small by
# design (a 1349-node cantilever keeps 878), and the fine mesh carries the reported peak.
# pillow_block's 355 kept nodes (2.5% of its nodes) are too few.
MIN_KEPT_FRACTION = 0.10
MIN_FINE_KEPT_NODES = 1000

# Strength-eligible: an interior peak and every convergence criterion.
SETTLED = "settled"
# Every convergence criterion holds, but the peak sits on the exclusion boundary: the stress there rises
# toward the support, so sigma_kept is information only and never a strength value.
SETTLED_AWAY_FROM_SUPPORT = "settled_away_from_support"
UNKNOWN = "unknown"

Point = tuple[float, float, float]


@dataclass(frozen=True)
class FrdResult:
    """Nodal coordinates, von Mises stress and displacement magnitude of one solve, keyed by node id."""

    coordinates: dict[int, Point]
    von_mises: dict[int, float]
    displacement: dict[int, float]


@dataclass(frozen=True)
class Level:
    """One mesh of the study.

    `fixed_nodes` and `load_nodes` are the node ids on the fixed and load faces; `plane_node_ids`
    selects them by coordinates. `high_order_fallback` is the element-order fallback level the mesher
    used (None when none), which must match between the two meshes.
    """

    h: float
    result: FrdResult
    fixed_nodes: frozenset[int]
    load_nodes: frozenset[int]
    high_order_fallback: int | None = None


@dataclass(frozen=True)
class LevelPeak:
    """The kept-region summary of one mesh."""

    h: float
    sigma_kept: float | None
    hot_spot: Point | None
    nearest_face: str | None
    distance_to_face: float | None
    kept_count: int
    node_count: int
    max_displacement: float

    @property
    def kept_share(self) -> float:
        return self.kept_count / self.node_count if self.node_count else 0.0


@dataclass(frozen=True)
class StressStudy:
    """Outcome of the comparison.

    `sigma_mpa` is the strength value and is set only when `status` is `settled`. A
    `settled_away_from_support` result carries `sigma_info_mpa` instead: information, never a strength
    value, so `strength_eligible` is False. An UNKNOWN result carries no number.
    """

    status: str
    sigma_mpa: float | None
    sigma_info_mpa: float | None
    reasons: tuple[str, ...]
    message: str
    coarse: LevelPeak
    fine: LevelPeak
    delta_sigma: float | None = None
    delta_displacement: float | None = None
    hot_spot_shift: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def strength_eligible(self) -> bool:
        return self.status == SETTLED


# --- .frd reading -------------------------------------------------------------------------------

def _values(line: str) -> list[float]:
    """Fixed-width ` -1` record: a 10-character node id, then 12-character values."""
    body = line[13:].rstrip("\n")
    return [float(body[i:i + 12]) for i in range(0, len(body), 12) if body[i:i + 12].strip()]


class FrdError(ValueError):
    """The .frd text is incomplete or malformed; its results cannot be trusted."""


def _records(lines: list[str], start: int, block: str) -> Iterable[tuple[int, list[float]]]:
    """Yield the ` -1` records of the block opened just before `start`; the block must end with ` -3`."""
    for line in lines[start:]:
        if line.startswith(" -3"):
            return
        if line.startswith(" -1"):
            yield int(line[3:13]), _values(line)
        elif not line.startswith((" -2", " -5")):
            raise FrdError(f"{block} block is cut off by {line.strip()[:20] or 'a blank line'!r}")
    raise FrdError(f"{block} block is cut off by the end of the file")


def _von_mises(s: list[float]) -> float:
    if len(s) < 6:
        raise FrdError(f"a STRESS record has {len(s)} of 6 components")
    sxx, syy, szz, sxy, syz, szx = s[:6]
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                     + 3.0 * (sxy ** 2 + syz ** 2 + szx ** 2))


def read_frd(text: str) -> FrdResult:
    """Parse node coordinates, von Mises stress and displacement magnitude from ccx `.frd` text.

    The last STRESS and DISP blocks are used (the final increment). Raises `FrdError` (a ValueError) when
    the file has no node, stress or displacement block, or when a block lacks its ` -3` terminator.
    Whether every node has a result is checked by `compare_levels`.
    """
    lines = text.splitlines()
    coordinates: dict[int, Point] = {}
    stress: dict[int, float] = {}
    displacement: dict[int, float] = {}
    for index, line in enumerate(lines):
        if line.startswith("    2C"):
            coordinates = {node: (v[0], v[1], v[2]) for node, v in _records(lines, index + 1, 'node')}
        elif line.startswith(" -4  STRESS"):
            stress = {node: _von_mises(v) for node, v in _records(lines, index + 1, 'STRESS')}
        elif line.startswith(" -4  DISP"):
            displacement = {node: math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
                            for node, v in _records(lines, index + 1, 'DISP')}
    if not coordinates or not stress or not displacement:
        raise FrdError("frd file lacks a node, STRESS or DISP block")
    return FrdResult(coordinates, stress, displacement)


def plane_node_ids(result: FrdResult, axis: str, value: float, tolerance: float = 1e-6) -> frozenset[int]:
    """Ids of the nodes on the plane `axis` (x, y or z) = `value`, within `tolerance`."""
    column = "xyz".index(axis)
    return frozenset(node for node, p in result.coordinates.items() if abs(p[column] - value) <= tolerance)


# --- distance to the boundary-condition faces ---------------------------------------------------

class _FaceIndex:
    """Grid hash of the boundary-condition nodes: nearest-node queries bounded by `radius`.

    The cell edge equals the radius, so a query visits only the 27 cells around the point. A point with no
    boundary node within `radius` gets `(inf, None)`; every distance is otherwise exact.
    """

    def __init__(self, faces: Mapping[str, Iterable[Point]], radius: float):
        self._radius = radius
        self._cells: dict[tuple[int, int, int], list[tuple[Point, str]]] = {}
        for name, points in faces.items():
            for p in points:
                self._cells.setdefault(self._cell(p), []).append((p, name))

    def _cell(self, p: Point) -> tuple[int, int, int]:
        r = self._radius
        return (math.floor(p[0] / r), math.floor(p[1] / r), math.floor(p[2] / r))

    def nearest(self, p: Point) -> tuple[float, str | None]:
        cx, cy, cz = self._cell(p)
        best, name = math.inf, None
        for dx, dy, dz in product((-1, 0, 1), repeat=3):
            for q, face in self._cells.get((cx + dx, cy + dy, cz + dz), ()):
                d = math.dist(p, q)
                if d < best:
                    best, name = d, face
        return (best, name) if best <= self._radius else (math.inf, None)


def _kept_peak(level: Level, cutoff: float, band: float) -> LevelPeak:
    """Peak von Mises over the nodes farther than `cutoff` from every fixed and load node."""
    result = level.result
    # One query radius covers both the cutoff and the boundary-governed band just beyond it.
    radius = cutoff + band
    index = _FaceIndex({"fixed": [result.coordinates[n] for n in level.fixed_nodes if n in result.coordinates],
                        "load": [result.coordinates[n] for n in level.load_nodes if n in result.coordinates]},
                       radius)
    kept_count = 0
    best: tuple[float, Point, float, str | None] | None = None
    for node, point in result.coordinates.items():
        distance, face = index.nearest(point)
        if distance <= cutoff:
            continue
        kept_count += 1
        sigma = result.von_mises.get(node, math.nan)
        if best is None or sigma > best[0]:
            best = (sigma, point, distance, face)
    max_displacement = max(result.displacement.values(), default=math.nan)
    if best is None:
        return LevelPeak(level.h, None, None, None, None, 0, len(result.coordinates), max_displacement)
    sigma, point, distance, face = best
    return LevelPeak(level.h, sigma, point, face, distance, kept_count, len(result.coordinates), max_displacement)


# --- the comparison -----------------------------------------------------------------------------

def _input_problems(label: str, level: Level) -> list[tuple[str, str]]:
    """(reason, detail) pairs for a mesh whose results cannot be compared: gaps, then non-finite values."""
    result = level.result
    problems = []
    for block, values in (("STRESS", result.von_mises), ("DISP", result.displacement)):
        missing = sum(1 for node in result.coordinates if node not in values)
        if missing:
            problems.append(("incomplete_results",
                             f"incomplete results: block {block} missing {missing} nodes ({label} mesh)"))
    if not all(math.isfinite(c) for p in result.coordinates.values() for c in p):
        problems.append(("non_finite_coordinates", f"node coordinates are not finite ({label} mesh)"))
    if not all(math.isfinite(v) for v in (*result.von_mises.values(), *result.displacement.values())):
        problems.append(("non_finite_stress", f"stress or displacement is not finite ({label} mesh)"))
    return problems


def _empty_peak(level: Level) -> LevelPeak:
    return LevelPeak(level.h, None, None, None, None, 0, len(level.result.coordinates), math.nan)


def _nearest_face(peak: LevelPeak, level: Level) -> str:
    """Name the nearest face of the hot spot, falling back to the exact nearest node when it is far."""
    if peak.nearest_face is not None:
        return f"{peak.nearest_face} face at {peak.distance_to_face:.3g} mm"
    if peak.hot_spot is None:
        return "no hot spot"
    best = min(((math.dist(peak.hot_spot, level.result.coordinates[n]), name)
                for name, nodes in (("fixed", level.fixed_nodes), ("load", level.load_nodes))
                for n in nodes if n in level.result.coordinates), default=(math.inf, "none"))
    return f"{best[1]} face at {best[0]:.3g} mm"


def _fmt(value: float | None, spec: str = ".4g") -> str:
    return "n/a" if value is None else format(value, spec)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.2f}%"


def _change(coarse: float, fine: float) -> float:
    """Change from the fine value to the coarse one, relative to the fine one."""
    if fine > 0:
        return abs(coarse - fine) / fine
    return 0.0 if coarse == fine else math.inf


def _screen(coarse: Level, fine: Level, c: LevelPeak, f: LevelPeak) -> list[str]:
    """Reasons that hold whatever the peaks are: a fallback mismatch, a thin kept region."""
    reasons = []
    if coarse.high_order_fallback != fine.high_order_fallback:
        reasons.append("high_order_fallback_differs")
    if min(c.kept_share, f.kept_share) < MIN_KEPT_FRACTION or f.kept_count < MIN_FINE_KEPT_NODES:
        reasons.append("kept_region_too_small")
    return reasons


@dataclass(frozen=True)
class _PeakComparison:
    reasons: list[str]
    delta_sigma: float
    shift: float
    on_cutoff: bool


def _compare_peaks(h_coarse: float, cutoff: float, c: LevelPeak, f: LevelPeak) -> _PeakComparison | None:
    """Compare the kept peaks of the two meshes, or None when either mesh has no kept node."""
    if c.sigma_kept is None or f.sigma_kept is None or c.hot_spot is None or f.hot_spot is None:
        return None
    reasons = []
    delta_sigma = _change(c.sigma_kept, f.sigma_kept)
    if delta_sigma > SETTLE_TOLERANCE:
        reasons.append("stress_not_settled")
    distances = [p.distance_to_face for p in (c, f) if p.distance_to_face is not None]
    # Governed by a support or load: the peak is within h_coarse of an actual face.
    # Fires only when K < 1; at K = 3 every kept node is at least 3·h_coarse from a face.
    if any(d <= h_coarse for d in distances):
        reasons.append("boundary_governed")
    # On the exclusion boundary: the peak is within h_coarse of the cutoff.
    on_cutoff = any(d <= cutoff + h_coarse for d in distances)
    shift = math.dist(c.hot_spot, f.hot_spot)
    # Hot-spot movement is compared only for an interior peak: on the cutoff it follows the cutoff.
    if not on_cutoff and shift > h_coarse:
        reasons.append("hot_spot_moved")
    return _PeakComparison(reasons, delta_sigma, shift, on_cutoff)


def _describe(coarse: Level, fine: Level, c: LevelPeak, f: LevelPeak, delta_sigma: float | None,
              delta_displacement: float | None, shift: float | None) -> str:
    """The figures every outcome names."""
    hot = f.hot_spot or c.hot_spot
    return (
        f"h_coarse={coarse.h:g} mm h_fine={fine.h:g} mm; "
        f"sigma_kept coarse={_fmt(c.sigma_kept)} MPa fine={_fmt(f.sigma_kept)} MPa; "
        f"change={_pct(delta_sigma)}; displacement change={_pct(delta_displacement)}; "
        f"hot spot={'n/a' if hot is None else tuple(round(v, 3) for v in hot)}"
        f" moved {_fmt(shift)} mm; nearest face (fine)={_nearest_face(f, fine)}; "
        f"kept region coarse={c.kept_count} nodes ({100 * c.kept_share:.1f}%), "
        f"fine={f.kept_count} nodes ({100 * f.kept_share:.1f}%) "
        f"(floor {100 * MIN_KEPT_FRACTION:g}% on both, {MIN_FINE_KEPT_NODES} nodes on fine)")


def compare_levels(coarse: Level, fine: Level) -> StressStudy:
    """Compare the coarse and fine solves; return the settled peak or UNKNOWN with the reasons.

    Raises ValueError when `coarse.h` is not larger than `fine.h`.
    """
    if not coarse.h > fine.h > 0:
        raise ValueError(f"coarse h ({coarse.h}) must exceed fine h ({fine.h}) and both must be positive")
    problems = _input_problems("coarse", coarse) + _input_problems("fine", fine)
    if problems:
        # Nothing is indexed or compared: gaps and non-finite values would make any peak a guess.
        reasons = tuple(dict.fromkeys(reason for reason, _ in problems))
        message = (f"UNKNOWN ({', '.join(reasons)}): h_coarse={coarse.h:g} mm h_fine={fine.h:g} mm; "
                   + "; ".join(detail for _, detail in problems))
        return StressStudy(UNKNOWN, None, None, reasons, message, _empty_peak(coarse), _empty_peak(fine))
    cutoff = K_EXCLUSION * coarse.h
    c, f = _kept_peak(coarse, cutoff, coarse.h), _kept_peak(fine, cutoff, coarse.h)

    reasons = _screen(coarse, fine, c, f)
    peaks = _compare_peaks(coarse.h, cutoff, c, f)
    if peaks:
        reasons += peaks.reasons
    delta_displacement = None
    if math.isfinite(c.max_displacement) and math.isfinite(f.max_displacement):
        delta_displacement = _change(c.max_displacement, f.max_displacement)
        if delta_displacement > SETTLE_TOLERANCE:
            reasons.append("displacement_not_settled")

    delta_sigma, shift = (peaks.delta_sigma, peaks.shift) if peaks else (None, None)
    message = _describe(coarse, fine, c, f, delta_sigma, delta_displacement, shift)
    extras = {"cutoff_mm": cutoff, "coarse_fallback": coarse.high_order_fallback,
              "fine_fallback": fine.high_order_fallback}
    if reasons or peaks is None or c.sigma_kept is None or f.sigma_kept is None:
        unique = tuple(dict.fromkeys(reasons))
        return StressStudy(UNKNOWN, None, None, unique, f"UNKNOWN ({', '.join(unique)}): {message}", c, f,
                           delta_sigma, delta_displacement, shift, extras)
    sigma = max(c.sigma_kept, f.sigma_kept)
    if peaks.on_cutoff:
        note = ("governed by the exclusion boundary, the support stress is higher; see #209; "
                "sigma_kept is information only, not a strength value")
        return StressStudy(SETTLED_AWAY_FROM_SUPPORT, None, sigma, ("exclusion_boundary",),
                           f"settled_away_from_support: {note}: {message}", c, f,
                           delta_sigma, delta_displacement, shift, extras)
    return StressStudy(SETTLED, sigma, None, (), f"settled: {message}", c, f,
                       delta_sigma, delta_displacement, shift, extras)
