#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Strength near a support or a load: linearised stress on SCLs a zone's depth inside the part, each gated by a
section-resultant cross-check.

The meshes are C3D10 bars built by test_fea_linearise.plate. Each node carries the stress of Euler-Bernoulli beam
theory with its parabolic shear, a quadratic field a C3D10 mesh holds exactly, so every expected value below comes
from the beam formulae, not from the code. The bar is 30 mm long (x), 10 mm wide (y) and 5 mm deep (z), clamped at
x = 0 and loaded with P = 50 N in -z over the nodes at x = 30. With I = 10 * 5^3 / 12 = 104.1667 mm^4, the moment at
x is M = P (30 - x) and the bending stress at the surfaces (z = 0 and 5) is M * 2.5 / I. The shear is
tau = -P (2.5^2 - zeta^2) / (2 I) on the x face, zeta = z - 2.5, which integrates to -P over the section.

Mesh size h = 1 mm, so the zone is max(t_local, 3 h) = max(5, 3) = 5 mm deep: the SCLs sit at x = 5 (root) and
x = 25 (load). Root: M = 1250 N mm, bending 30.0 MPa, mean shear 1.0 MPa. Load: M = 250 N mm, bending 6.0 MPa.
The membrane plus bending von Mises stress is sqrt(bending^2 + 3 * shear^2): sqrt(903) = 30.0500 at the root and
sqrt(39) = 6.2450 at the load.
"""

from __future__ import annotations

import gzip
import importlib.util
import math
import sys
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent / "scripts"))

import fea_near_support as ns  # noqa: E402
import fea_stress as fs  # noqa: E402
import fea_stress_study as study  # noqa: E402


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = sys.modules.get(name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


linearise_tests = _sibling("test_fea_linearise")

Point = tuple[float, float, float]
LENGTH, WIDTH, DEPTH = 30.0, 10.0, 5.0
LOAD = 50.0
INERTIA = WIDTH * DEPTH ** 3 / 12.0


def bar(cells_x: int = 30) -> tuple[dict[int, Point], dict[int, study.Element]]:
    return linearise_tests.plate("C3D10", (cells_x, 2, 2), (LENGTH, WIDTH, DEPTH))


def beam_field(load: float = LOAD, bending: float = 1.0, shear: float = 1.0
               ) -> Callable[[Point], tuple[float, float, float, float, float, float]]:
    """The beam-theory tensor at a point, in the order ccx writes it; `bending` and `shear` scale the two parts."""
    def field(p: Point) -> tuple[float, float, float, float, float, float]:
        zeta = p[2] - DEPTH / 2
        sxx = bending * load * (LENGTH - p[0]) * zeta / INERTIA
        szx = -shear * load * (DEPTH ** 2 / 4 - zeta ** 2) / (2 * INERTIA)
        return (sxx, 0.0, 0.0, 0.0, 0.0, szx)
    return field


def result_of(coordinates: Mapping[int, Point], field: Callable[[Point], tuple[float, ...]]) -> fs.FrdResult:
    return fs.FrdResult(dict(coordinates), {}, {}, {n: fs.StressTensor(*field(p)) for n, p in coordinates.items()})


def nodes_at(coordinates: Mapping[int, Point], axis: int, value: float) -> frozenset[int]:
    return frozenset(n for n, p in coordinates.items() if abs(p[axis] - value) < 1e-9)


def force_on(nodes: frozenset[int], fz: float) -> dict[int, tuple[float, float, float]]:
    """`fz` newtons in z, shared equally by `nodes`."""
    return {n: (0.0, 0.0, fz / len(nodes)) for n in nodes}


class Cantilever(unittest.TestCase):
    """The bar of the module docstring, clamped at x = 0 and loaded at x = 30."""

    def setUp(self) -> None:
        self.coordinates, self.elements = bar()
        self.fixed = nodes_at(self.coordinates, 0, 0.0)
        self.loads = [force_on(nodes_at(self.coordinates, 0, LENGTH), -LOAD)]

    def assess(self, field: Callable[[Point], tuple[float, ...]] | None = None, **kwargs: Any) -> ns.NearSupport:
        given: dict[str, Any] = {"fixed": self.fixed, "loads": self.loads, "h_coarse": 1.0, "h_mesh": 1.0}
        given.update(kwargs)
        return ns.assess(result_of(self.coordinates, field or beam_field()), self.elements, **given)

    def section(self, found: ns.NearSupport, face: str) -> ns.Section:
        return next(s for s in found.sections if s.face == face)

    def test_the_root_section_gives_the_beam_theory_membrane_plus_bending_stress(self) -> None:
        found = self.assess()
        self.assertEqual((found.status, found.reasons), ("linearised", ()))
        root = self.section(found, "fixed")
        self.assertAlmostEqual(root.section_mm, 5.0, places=9)
        self.assertAlmostEqual(root.sigma_mpa, math.sqrt(30.0 ** 2 + 3 * 1.0 ** 2), places=3)

    def test_a_load_face_is_treated_as_a_fixed_face_with_its_own_section(self) -> None:
        found = self.assess()
        tip = self.section(found, "load")
        self.assertAlmostEqual(tip.section_mm, 25.0, places=9)
        self.assertAlmostEqual(tip.sigma_mpa, math.sqrt(6.0 ** 2 + 3 * 1.0 ** 2), places=3)

    def test_the_strength_stress_is_the_largest_of_the_sections_and_no_shape_factor_credits_the_bending(self) -> None:
        # 1.5 times the root bending would give 20 MPa from 30.05: the full 30.05 is the figure.
        found = self.assess()
        assert found.sigma_mpa is not None
        self.assertAlmostEqual(found.sigma_mpa, 30.0500, places=3)

    def test_the_peak_stress_at_the_section_ends_is_reported_and_is_not_the_strength_stress(self) -> None:
        # The exact field is linear in the bending, so the quadratic remainder is the shear's: at a surface the
        # total von Mises is the bending alone, 30.0 MPa, below the linearised 30.05, and it is reported as is.
        root = self.section(self.assess(), "fixed")
        self.assertAlmostEqual(root.peak_mpa, 30.0, places=3)

    def test_the_residuals_of_an_exact_field_are_zero(self) -> None:
        for section in self.assess().sections:
            with self.subTest(section.face):
                self.assertLess(section.force_residual, 1e-4)
                self.assertLess(section.moment_residual, 1e-4)

    def test_the_cross_check_passes_a_moment_inside_its_tolerance_and_refuses_one_outside(self) -> None:
        # The moment scale is sum |r x f|, about 0.8% above P * 25 at the root, so 4% passes and 6% does not.
        self.assertEqual(self.assess(beam_field(bending=1.04)).status, "linearised")
        refused = self.assess(beam_field(bending=1.06))
        self.assertEqual((refused.status, refused.reasons, refused.sigma_mpa), ("unknown", ("cross_check_failed",), None))

    def test_the_cross_check_refuses_a_section_whose_shear_does_not_carry_the_load(self) -> None:
        # Half the shear: the section force is 25 N against 50 N applied, 50% of the force scale.
        refused = self.assess(beam_field(shear=0.5))
        self.assertEqual((refused.status, refused.reasons), ("unknown", ("cross_check_failed",)))
        self.assertIn("force", refused.message)

    def test_a_cross_check_failure_names_the_face_and_both_residuals(self) -> None:
        refused = self.assess(beam_field(bending=1.2))
        self.assertIn("fixed face", refused.message)
        self.assertRegex(refused.message, r"moment residual 1[89]\.\d+%")

    def test_a_part_with_a_second_fixed_face_is_statically_indeterminate_and_abstains(self) -> None:
        # Clamped at both ends and loaded across the mid-plane x = 15: the reactions are not statics' to give.
        self.fixed = nodes_at(self.coordinates, 0, 0.0) | nodes_at(self.coordinates, 0, LENGTH)
        self.loads = [force_on(nodes_at(self.coordinates, 0, 15.0), -LOAD)]
        found = self.assess()
        self.assertEqual((found.status, found.reasons, found.sigma_mpa), ("unknown", ("statically_indeterminate",), None))

    def test_a_refusal_of_the_linearisation_is_unknown_with_its_reason_and_never_a_figure(self) -> None:
        # No element between x = 4 and 6: the root SCL at x = 5 runs through a void.
        self.elements = {i: e for i, e in self.elements.items()
                         if not 4.0 < sum(self.coordinates[n][0] for n in e.nodes[:4]) / 4 < 6.0}
        found = self.assess()
        self.assertEqual((found.status, found.reasons, found.sigma_mpa), ("unknown", ("scl_outside_mesh",), None))

    def test_a_result_without_a_stress_tensor_is_unknown_with_the_linearisations_reason(self) -> None:
        bare = fs.FrdResult(dict(self.coordinates), {}, {}, {})
        found = ns.assess(bare, self.elements, fixed=self.fixed, loads=self.loads, h_coarse=1.0, h_mesh=1.0)
        self.assertEqual((found.status, found.reasons), ("unknown", ("scl_tensor_missing",)))

    def test_two_load_entries_on_one_node_and_degree_of_freedom_abstain(self) -> None:
        node = next(iter(self.loads[0]))
        self.loads = [*self.loads, {node: (0.0, 0.0, -1.0)}]
        self.assertEqual(self.assess().reasons, ("load_nodes_overlap",))

    def test_a_case_without_an_applied_force_abstains(self) -> None:
        self.loads = [{n: (0.0, 0.0, 0.0) for n in self.loads[0]}]
        self.assertEqual(self.assess().reasons, ("no_applied_load",))

    def test_a_fixed_face_that_is_not_flat_abstains(self) -> None:
        stray = next(n for n, p in self.coordinates.items() if abs(p[0] - 1.0) < 1e-9)
        self.fixed = self.fixed | {stray}
        self.assertEqual(self.assess().reasons, ("support_not_axis_planar",))

    def test_a_part_shorter_than_the_zone_finds_no_section(self) -> None:
        self.assertEqual(self.assess(h_coarse=20.0).reasons, ("section_outside_part",))


class ZoneDepth(unittest.TestCase):
    def test_the_zone_is_the_larger_of_one_local_thickness_and_three_coarse_mesh_sizes(self) -> None:
        for thickness, h_coarse, depth in ((5.0, 1.0, 5.0), (5.0, 4.5, 13.5), (2.0, 1.0, 3.0), (3.0, 1.0, 3.0)):
            with self.subTest(thickness=thickness, h_coarse=h_coarse):
                self.assertAlmostEqual(ns.zone_depth(thickness, h_coarse), depth, places=12)


class LoadBlocks(unittest.TestCase):
    def test_each_cload_block_is_one_face_with_its_forces_summed_per_node_and_zero_entries_kept(self) -> None:
        inp = ("*NSET,NSET=FixedFaces\n1,\n** FaceForce0\n*CLOAD\n** node loads on shape: Part:Face2\n"
               "5,3,-0\n6,3,-2.5\n6,1,1.5\n** FaceForce1\n*CLOAD\n7,2,4.0\n")
        self.assertEqual(study.load_blocks(inp), [{5: (0.0, 0.0, 0.0), 6: (1.5, 0.0, -2.5)}, {7: (0.0, 4.0, 0.0)}])

    def test_a_repeated_degree_of_freedom_in_one_block_is_refused(self) -> None:
        with self.assertRaises(study.BoundaryNodesError):
            study.load_blocks("*CLOAD\n5,3,1.0\n5,3,2.0\n")


class RealCantilever(unittest.TestCase):
    """The fast-bias cantilever at 4.5 mm: 100 x 10 x 5 mm, clamped at x = 0, 50 N in -z at x = 100.

    The zone is max(5, 3 * 4.5) = 13.5 mm. At x = 13.5 the moment is 50 * 86.5 = 4325 N mm, so Mc/I = 4325 * 2.5 /
    104.1667 = 103.8 MPa. Near the load, at x = 86.5, M = 675 and Mc/I = 16.2 MPa. The solve's nodal stresses are
    smoothed, so the allowed distance is 3.5% of the root figure."""

    def test_the_root_and_load_sections_pass_the_cross_check_and_give_mc_over_i(self) -> None:
        with gzip.open(FIXTURES / "cantilever_r1_4.5mm.frd.gz", "rt") as handle:
            result = fs.read_frd(handle.read())
        elements = study.element_connectivity((FIXTURES / "cantilever_r1_4.5mm.elements.inp").read_text(encoding="utf-8"))
        faces = (FIXTURES / "cantilever_r1_4.5mm.faces.inp").read_text(encoding="utf-8")
        fixed, _ = study.boundary_nodes(faces)
        found = ns.assess(result, elements, fixed=fixed, loads=study.load_blocks(faces), h_coarse=4.5, h_mesh=4.5)
        self.assertEqual((found.status, found.reasons), ("linearised", ()))
        by_face = {s.face: s for s in found.sections}
        self.assertAlmostEqual(by_face["fixed"].section_mm, 13.5, places=6)
        self.assertAlmostEqual(by_face["load"].section_mm, 86.5, places=6)
        self.assertAlmostEqual(by_face["fixed"].sigma_mpa, 103.8, delta=3.6)
        self.assertAlmostEqual(by_face["load"].sigma_mpa, 16.2, delta=1.0)
        for section in found.sections:
            self.assertLess(section.force_residual, ns.FORCE_TOLERANCE)
            self.assertLess(section.moment_residual, ns.MOMENT_TOLERANCE)


if __name__ == "__main__":
    unittest.main()
