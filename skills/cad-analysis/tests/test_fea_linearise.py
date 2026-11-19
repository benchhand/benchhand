#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Stress linearisation along a stress classification line, against closed-form through-thickness fields.

The meshes are built here: a plate of hexahedral cells, each split into six tetrahedra, as C3D4 or C3D10. Each node
carries the chosen field's tensor at its position. A C3D10 mesh interpolates a quadratic field exactly, and a C3D4
mesh a linear one, so each expected value comes from the field's formula, not from the code. The plate is
`THICKNESS` thick along z, and the line runs through it from z = 0 to z = THICKNESS. Through the thickness,
xi = 2 z / THICKNESS - 1 runs from -1 at the start of the line to +1 at its end.
"""

from __future__ import annotations

import gzip
import math
import sys
import unittest
from collections.abc import Callable
from itertools import permutations, product
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import fea_linearise as fl  # noqa: E402
import fea_stress as fs  # noqa: E402
import fea_stress_study as study  # noqa: E402

Point = tuple[float, float, float]
Field = Callable[[Point], tuple[float, float, float, float, float, float]]

# ccx 2.23 manual, Figure 63 (section 6.2.7): C3D10 node 5 sits on edge 1-2, 6 on 2-3, 7 on 3-1, 8 on 1-4, 9 on
# 2-4 and 10 on 3-4.
C3D10_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
SIZE = (4.0, 4.0, 2.0)
THICKNESS = SIZE[2]
START, END = (1.3, 2.1, 0.0), (1.3, 2.1, THICKNESS)


def xi(p: Point) -> float:
    return 2.0 * p[2] / THICKNESS - 1.0


def _volume(a: Point, b: Point, c: Point, d: Point) -> float:
    u, v, w = ([q[k] - a[k] for k in range(3)] for q in (b, c, d))
    return (u[0] * (v[1] * w[2] - v[2] * w[1]) - u[1] * (v[0] * w[2] - v[2] * w[0])
            + u[2] * (v[0] * w[1] - v[1] * w[0]))


def plate(kind: str = "C3D10", cells: tuple[int, int, int] = (2, 2, 4)
          ) -> tuple[dict[int, Point], dict[int, study.Element]]:
    """A SIZE plate of `cells` hexahedra, each split into the six tetrahedra along its main diagonal.

    Every hexahedron splits the same way, so the faces of neighbouring tetrahedra match. Each tetrahedron is
    ordered to a positive volume, as ccx needs, and a C3D10 shares each mid-edge node with its neighbours."""
    counts = [n + 1 for n in cells]
    grid = {ijk: i + 1 for i, ijk in enumerate(product(*(range(n) for n in counts)))}
    coordinates = {node: tuple(SIZE[k] * ijk[k] / cells[k] for k in range(3)) for ijk, node in grid.items()}
    middles: dict[frozenset[int], int] = {}
    elements: dict[int, study.Element] = {}
    for cell in product(*(range(n) for n in cells)):
        for order in permutations(range(3)):
            step, path = list(cell), [tuple(cell)]
            for axis in order:
                step[axis] += 1
                path.append(tuple(step))
            corners = [grid[p] for p in path]
            if _volume(*(coordinates[c] for c in corners)) < 0:  # type: ignore[arg-type]
                corners[1], corners[2] = corners[2], corners[1]
            nodes = list(corners)
            for a, b in C3D10_EDGES if kind == "C3D10" else ():
                edge = frozenset((corners[a], corners[b]))
                if edge not in middles:
                    middles[edge] = 1_000_000 + len(middles)
                    coordinates[middles[edge]] = tuple((coordinates[corners[a]][k] + coordinates[corners[b]][k]) / 2
                                                       for k in range(3))
                nodes.append(middles[edge])
            elements[len(elements) + 1] = study.Element(kind, tuple(nodes))
    return coordinates, elements  # type: ignore[return-value]


def frd(coordinates: dict[int, Point], field: Field) -> fs.FrdResult:
    return fs.FrdResult(dict(coordinates), {}, {}, {n: fs.StressTensor(*field(p)) for n, p in coordinates.items()})


def scl(*values: float) -> fl.SclStress:
    return fl.SclStress(*values)


ZERO = scl(0, 0, 0, 0, 0, 0)


class Case(unittest.TestCase):
    def assertTensor(self, actual: fl.SclStress, expected: fl.SclStress, places: int = 9) -> None:
        for name, a, e in zip(fl.SclStress._fields, actual, expected):
            self.assertAlmostEqual(a, e, places=places, msg=f"{name}: {actual} != {expected}")

    def linearise(self, field: Field, kind: str = "C3D10", **kwargs: object) -> fl.Linearisation:
        coordinates, elements = plate(kind)
        return fl.linearise(frd(coordinates, field), elements, START, END, **kwargs)  # type: ignore[arg-type]


class ClosedFormTests(Case):
    def test_uniform_tension_is_all_membrane(self) -> None:
        for kind in ("C3D4", "C3D10"):
            with self.subTest(kind=kind):
                r = self.linearise(lambda p: (50.0, 0, 0, 0, 0, 0), kind)
                self.assertTensor(r.membrane, scl(50, 0, 0, 0, 0, 0))
                self.assertTensor(r.bending, ZERO)
                self.assertTensor(r.peak_start, ZERO)
                self.assertTensor(r.peak_end, ZERO)
                for vm in (r.von_mises_membrane, r.von_mises_membrane_bending_start, r.von_mises_membrane_bending_end,
                           r.von_mises_total_start, r.von_mises_total_end):
                    self.assertAlmostEqual(vm, 50.0, places=9)

    def test_pure_bending_of_a_plate_is_all_bending(self) -> None:
        # sigma_xx = -80 xi: +80 at the start of the line, -80 at its end, zero mean.
        for kind in ("C3D4", "C3D10"):
            with self.subTest(kind=kind):
                r = self.linearise(lambda p: (-80.0 * xi(p), 0, 0, 0, 0, 0), kind)
                self.assertTensor(r.membrane, ZERO)
                self.assertTensor(r.bending, scl(80, 0, 0, 0, 0, 0))
                self.assertTensor(r.peak_start, ZERO)
                self.assertTensor(r.peak_end, ZERO)
                self.assertAlmostEqual(r.von_mises_membrane, 0.0, places=9)
                self.assertAlmostEqual(r.von_mises_membrane_bending_start, 80.0, places=9)
                self.assertAlmostEqual(r.von_mises_membrane_bending_end, 80.0, places=9)

    def test_membrane_and_bending_combine_at_each_end(self) -> None:
        # sigma_xx = 30 - 80 xi, sigma_yy = -10 - 10 xi: 110 and 0 at the start, -50 and -20 at the end. In plane
        # stress the von Mises stress is sqrt(a^2 - a b + b^2).
        r = self.linearise(lambda p: (30.0 - 80.0 * xi(p), -10.0 - 10.0 * xi(p), 0, 0, 0, 0))
        self.assertTensor(r.membrane, scl(30, -10, 0, 0, 0, 0))
        self.assertTensor(r.bending, scl(80, 10, 0, 0, 0, 0))
        self.assertAlmostEqual(r.von_mises_membrane, math.sqrt(30 ** 2 + 30 * 10 + 10 ** 2), places=9)
        self.assertAlmostEqual(r.von_mises_membrane_bending_start, 110.0, places=9)
        self.assertAlmostEqual(r.von_mises_membrane_bending_end, math.sqrt(50 ** 2 - 50 * 20 + 20 ** 2), places=9)

    def test_a_parabolic_distribution_leaves_its_closed_form_remainder_as_peak(self) -> None:
        # sigma_xx = 90 xi^2: the mean of xi^2 over [-1, 1] is 1/3, so membrane 30. It is even, so bending 0.
        # The remainder 90 (xi^2 - 1/3) is 60 at each end, where the total is 90.
        r = self.linearise(lambda p: (90.0 * xi(p) ** 2, 0, 0, 0, 0, 0))
        self.assertTensor(r.membrane, scl(30, 0, 0, 0, 0, 0))
        self.assertTensor(r.bending, ZERO)
        self.assertTensor(r.peak_start, scl(60, 0, 0, 0, 0, 0))
        self.assertTensor(r.peak_end, scl(60, 0, 0, 0, 0, 0))
        self.assertAlmostEqual(r.von_mises_membrane_bending_start, 30.0, places=9)
        self.assertAlmostEqual(r.von_mises_total_start, 90.0, places=9)
        self.assertAlmostEqual(r.von_mises_total_end, 90.0, places=9)

    def test_simpson_integrates_a_quadratic_field_exactly_where_the_trapezoid_rule_does_not(self) -> None:
        # sigma_xx = 90 xi^2 - 60 xi + 10: membrane 10 + 90 / 3 = 40, bending 60, peak 60 at each end.
        # The bending integrand is cubic, which Simpson's rule also integrates exactly. Over 21 points (step
        # 0.1 in xi) the trapezoid rule overstates the mean of 90 xi^2 by 90 * 0.1^2 / 6 = 0.15, so its membrane
        # would be 40.15, far outside the 1e-9 checked here.
        r = self.linearise(lambda p: (90.0 * xi(p) ** 2 - 60.0 * xi(p) + 10.0, 0, 0, 0, 0, 0), points=21)
        self.assertTensor(r.membrane, scl(40, 0, 0, 0, 0, 0))
        self.assertTensor(r.bending, scl(60, 0, 0, 0, 0, 0))
        self.assertTensor(r.peak_start, scl(60, 0, 0, 0, 0, 0))
        self.assertTensor(r.peak_end, scl(60, 0, 0, 0, 0, 0))

    def test_only_the_two_tangential_normal_components_carry_bending(self) -> None:
        # The normal stress and the three shears vary through the thickness, but Annex 5-A bends only the two
        # tangential normal stresses: the others are membrane plus peak. sigma_n = 5 - 40 xi, the in-plane shear
        # = -12 xi, sigma_t2n = 7 xi and sigma_nt1 = 30 (1 - xi^2), whose mean is 20.
        def field(p: Point) -> tuple[float, float, float, float, float, float]:
            x = xi(p)
            return (0.0, 0.0, 5.0 - 40.0 * x, -12.0 * x, 7.0 * x, 30.0 * (1.0 - x * x))
        r = self.linearise(field)
        self.assertTensor(r.membrane, scl(0, 0, 5, 0, 0, 20))
        self.assertTensor(r.bending, ZERO)
        self.assertTensor(r.peak_start, scl(0, 0, 40, 12, -7, -20))
        self.assertTensor(r.peak_end, scl(0, 0, -40, -12, 7, -20))
        membrane_vm = math.sqrt(5 ** 2 + 3 * 20 ** 2)
        self.assertAlmostEqual(r.von_mises_membrane_bending_start, membrane_vm, places=9)
        self.assertAlmostEqual(r.von_mises_total_start, math.sqrt(45 ** 2 + 3 * 12 ** 2 + 3 * 7 ** 2), places=9)

    def test_the_von_mises_of_the_membrane_comes_from_the_linearised_components(self) -> None:
        # The von Mises stress of a field 100 xi is 100 |xi|, whose own mean is 50. The membrane tensor is zero.
        r = self.linearise(lambda p: (100.0 * xi(p), 0, 0, 0, 0, 0))
        self.assertAlmostEqual(r.von_mises_membrane, 0.0, places=9)
        self.assertAlmostEqual(r.von_mises_total_end, 100.0, places=9)


def general_quadratic(p: Point) -> tuple[float, float, float, float, float, float]:
    """A different full quadratic in x, y and z for each component, with every cross term."""
    x, y, z = p
    return tuple(c + 2.0 * x - 3.0 * y + 5.0 * z + 0.7 * x * y - 1.1 * y * z + 1.3 * z * x
                 + (0.5 + c / 10) * x * x - 0.9 * y * y + (2.0 - c / 7) * z * z
                 for c in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))  # type: ignore[return-value]


def general_linear(p: Point) -> tuple[float, float, float, float, float, float]:
    x, y, z = p
    return tuple(c + 2.0 * x - 3.0 * y + (5.0 + c) * z for c in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))  # type: ignore[return-value]


class InterpolationTests(Case):
    """Along the z line the default axes are t1 = x, t2 = y and n = z, so each SCL component is a global one."""

    def test_each_point_takes_the_field_interpolated_at_its_position(self) -> None:
        for kind, field in (("C3D10", general_quadratic), ("C3D4", general_linear)):
            with self.subTest(kind=kind):
                r = self.linearise(field, kind)
                self.assertEqual(len(r.positions), fl.DEFAULT_POINTS)
                self.assertEqual(r.positions[0], START)
                self.assertEqual(r.positions[-1], END)
                for position, stress in zip(r.positions, r.stresses):
                    self.assertTensor(stress, scl(*field(position)))

    def test_a_c3d10_whose_mid_edge_node_is_off_centre_is_inverted_through_its_quadratic_map(self) -> None:
        # One element, corners on the axes at 2, with the edge 1-2 node moved from x = 1 to x = 1.3. The edge is
        # still straight, so the element fills the same tetrahedron, but its map from natural coordinates is no
        # longer affine. A field equal to each node's x coordinate interpolates to the point's own x only when the
        # point's natural coordinates come from that map.
        corners = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 2.0)]
        middles = [tuple((corners[a][k] + corners[b][k]) / 2 for k in range(3)) for a, b in C3D10_EDGES]
        middles[0] = (1.3, 0.0, 0.0)
        coordinates = dict(enumerate([*corners, *middles], start=1))
        elements = {1: study.Element("C3D10", tuple(range(1, 11)))}
        result = frd(coordinates, lambda p: (p[0], 0, 0, 0, 0, 0))  # type: ignore[arg-type]
        r = fl.linearise(result, elements, (0.9, 0.3, 0.1), (0.9, 0.3, 0.7))
        for position, stress in zip(r.positions, r.stresses):
            self.assertAlmostEqual(stress.t1, position[0], places=9)


def _rotation(axis: Point, angle: float) -> list[list[float]]:
    norm = math.sqrt(sum(a * a for a in axis))
    x, y, z = (a / norm for a in axis)
    c, s, v = math.cos(angle), math.sin(angle), 1.0 - math.cos(angle)
    return [[c + x * x * v, x * y * v - z * s, x * z * v + y * s],
            [y * x * v + z * s, c + y * y * v, y * z * v - x * s],
            [z * x * v - y * s, z * y * v + x * s, c + z * z * v]]


def _apply(m: list[list[float]], p: Point) -> Point:
    return tuple(sum(m[i][k] * p[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def _rotate_tensor(m: list[list[float]], s: tuple[float, ...]) -> tuple[float, float, float, float, float, float]:
    sxx, syy, szz, sxy, syz, szx = s
    full = [[sxx, sxy, szx], [sxy, syy, syz], [szx, syz, szz]]
    r = [[sum(m[i][a] * full[a][b] * m[j][b] for a in range(3) for b in range(3)) for j in range(3)] for i in range(3)]
    return (r[0][0], r[1][1], r[2][2], r[0][1], r[1][2], r[2][0])


class AxesTests(Case):
    def test_the_same_field_on_a_rotated_line_gives_the_same_linearisation(self) -> None:
        # The plate, its field and the line turn together about (1, 2, 2) by 0.7 rad, and the tangent hint turns
        # with them. The field has every component, so a result left in the global axes would differ.
        m = _rotation((1.0, 2.0, 2.0), 0.7)
        coordinates, elements = plate()
        reference = fl.linearise(frd(coordinates, general_quadratic), elements, START, END, tangent=(1.0, 0.0, 0.0))
        rotated_coordinates = {n: _apply(m, p) for n, p in coordinates.items()}
        result = fs.FrdResult(rotated_coordinates, {}, {}, {
            n: fs.StressTensor(*_rotate_tensor(m, general_quadratic(p))) for n, p in coordinates.items()})
        r = fl.linearise(result, elements, _apply(m, START), _apply(m, END), tangent=_apply(m, (1.0, 0.0, 0.0)))
        for name in ("membrane", "bending", "peak_start", "peak_end"):
            with self.subTest(name=name):
                self.assertTensor(getattr(r, name), getattr(reference, name), places=8)
        for name in ("von_mises_membrane", "von_mises_membrane_bending_start", "von_mises_membrane_bending_end",
                     "von_mises_total_start", "von_mises_total_end"):
            with self.subTest(name=name):
                self.assertAlmostEqual(getattr(r, name), getattr(reference, name), places=8)

    def test_the_default_tangent_is_the_global_axis_least_aligned_with_the_line(self) -> None:
        # n is along (1, 0.2, 3) / sqrt(10.04). Its smallest component is y, so t1 is the unit part of
        # (0, 1, 0) normal to n: (-0.2, 10, -0.6) / sqrt(100.4). t2 = n x t1.
        coordinates, elements = plate()
        r = fl.linearise(frd(coordinates, general_quadratic), elements, (1.0, 1.5, 0.3), (1.4, 1.58, 1.5))
        n = tuple(c / math.sqrt(10.04) for c in (1.0, 0.2, 3.0))
        t1 = tuple(c / math.sqrt(100.4) for c in (-0.2, 10.0, -0.6))
        t2 = (n[1] * t1[2] - n[2] * t1[1], n[2] * t1[0] - n[0] * t1[2], n[0] * t1[1] - n[1] * t1[0])
        for actual, expected in ((r.normal, n), (r.t1, t1), (r.t2, t2)):
            for a, e in zip(actual, expected):
                self.assertAlmostEqual(a, e, places=12)
        self.assertAlmostEqual(r.thickness, math.sqrt(0.4 ** 2 + 0.08 ** 2 + 1.2 ** 2), places=12)

    def test_a_tie_between_two_axes_takes_the_first_and_matches_the_explicit_tangent(self) -> None:
        coordinates, elements = plate()
        result = frd(coordinates, general_quadratic)
        default = fl.linearise(result, elements, START, END)
        explicit = fl.linearise(result, elements, START, END, tangent=(3.0, 0.0, 0.5))
        self.assertEqual((default.t1, default.t2, default.normal), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        self.assertEqual(default, explicit)


class RefusalTests(Case):
    def assertRefused(self, reason: fl.Refusal, call: Callable[[], object]) -> None:
        with self.assertRaises(fl.LinearisationError) as caught:
            call()
        self.assertIs(caught.exception.reason, reason)

    def test_each_input_it_cannot_stand_behind_is_refused_with_its_reason(self) -> None:
        coordinates, elements = plate()
        good = frd(coordinates, general_quadratic)
        node = elements[1].nodes[4]

        def with_stress(stress: dict[int, fs.StressTensor]) -> fs.FrdResult:
            return fs.FrdResult(good.coordinates, {}, {}, stress)

        def replaced(value: float) -> fs.FrdResult:
            return with_stress({**good.stress, node: good.stress[node]._replace(syz=value)})

        hexahedron = {i + 1: p for i, p in enumerate(product((0.0, 1.0), repeat=3))}
        cases = [
            ("no tensor at all", fl.Refusal.TENSOR_MISSING, with_stress({}), elements, START, END, {}),
            ("a node without its tensor", fl.Refusal.TENSOR_INCOMPLETE,
             with_stress({n: s for n, s in good.stress.items() if n != node}), elements, START, END, {}),
            ("a nan component", fl.Refusal.TENSOR_NOT_FINITE, replaced(math.nan), elements, START, END, {}),
            ("an infinite component", fl.Refusal.TENSOR_NOT_FINITE, replaced(-math.inf), elements, START, END, {}),
            ("an element node without coordinates", fl.Refusal.NODE_MISSING,
             fs.FrdResult({n: p for n, p in coordinates.items() if n != node}, {}, {}, good.stress),
             elements, START, END, {}),
            ("an end above the plate", fl.Refusal.OUTSIDE_MESH, good, elements, START, (1.3, 2.1, 2.5), {}),
            ("a line beside the plate", fl.Refusal.OUTSIDE_MESH, good, elements, (5.0, 2.1, 0.0), (5.0, 2.1, 2.0), {}),
            ("a point in a C3D8", fl.Refusal.UNSUPPORTED_ELEMENT, frd(hexahedron, general_linear),
             {1: study.Element("C3D8", tuple(hexahedron))}, (0.5, 0.5, 0.0), (0.5, 0.5, 1.0), {}),
            ("a C3D10 of four nodes", fl.Refusal.UNSUPPORTED_ELEMENT, good,
             {k: study.Element(e.kind, e.nodes[:4]) for k, e in elements.items()}, START, END, {}),
            ("a line of zero length", fl.Refusal.ZERO_LENGTH, good, elements, START, START, {}),
            ("a line with a nan end", fl.Refusal.LINE_NOT_FINITE, good, elements, START, (1.3, math.nan, 2.0), {}),
            ("19 points", fl.Refusal.TOO_FEW_POINTS, good, elements, START, END, {"points": 19}),
            ("an even number of points", fl.Refusal.EVEN_POINTS, good, elements, START, END, {"points": 22}),
            ("a tangent along the line", fl.Refusal.TANGENT_ALONG_LINE, good, elements, START, END,
             {"tangent": (0.0, 0.0, -2.0)}),
            ("a zero tangent", fl.Refusal.TANGENT_ALONG_LINE, good, elements, START, END, {"tangent": (0.0, 0.0, 0.0)}),
        ]
        for label, reason, result, mesh, start, end, kwargs in cases:
            with self.subTest(label):
                self.assertRefused(reason, lambda: fl.linearise(result, mesh, start, end, **kwargs))  # type: ignore[arg-type]

    def test_the_message_names_the_first_point_outside_the_mesh(self) -> None:
        # The line rises 2.5 over 20 steps, so point 18 (z = 2.125) is the first above the plate's z = 2.
        coordinates, elements = plate()
        with self.assertRaisesRegex(fl.LinearisationError, r"outside_mesh: point 18 of 21 at \(1\.3, 2\.1, 2\.125\)"):
            fl.linearise(frd(coordinates, general_quadratic), elements, START, (1.3, 2.1, 2.5))


class CantileverSanityCheck(unittest.TestCase):
    """A sanity check on a real solve, not an accuracy gate: the fast-bias cantilever at 4.5 mm.

    The beam is 100 x 10 x 5 mm, clamped at x = 0 and loaded with 50 N in -z at x = 100. At the root, M = 5000 N mm
    and I = 10 * 5^3 / 12, so Mc/I = 5000 * 2.5 / 104.17 = 120 MPa, compression at z = 0 and tension at z = 5. The
    line runs through the root face, where the clamp's singularity sits, on a 4.5 mm mesh of a 5 mm wall, so the
    check allows 5% of Mc/I (6 MPa). The fixture gives a bending stress of -121.5 MPa and a membrane of -0.02."""

    def test_the_bending_at_the_root_is_near_mc_over_i(self) -> None:
        result = fs.read_frd(gzip.decompress((FIXTURES / "cantilever_r1_4.5mm.frd.gz").read_bytes()).decode())
        inp = (FIXTURES / "cantilever_r1_4.5mm.elements.inp").read_text(encoding="utf-8")
        r = fl.linearise(result, study.element_connectivity(inp), (0.0, 5.0, 0.0), (0.0, 5.0, 5.0))
        self.assertAlmostEqual(r.bending.t1, -120.0, delta=6.0)
        self.assertLess(abs(r.membrane.t1), 6.0)


if __name__ == "__main__":
    unittest.main()
