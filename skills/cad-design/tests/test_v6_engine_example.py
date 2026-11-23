#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The V6 engine example (examples/v6-engine): its record's joint axes and the built crank train.

Run: python3 -m unittest discover -s skills/cad-design/tests -p test_v6_engine_example.py -v
The axis tests read the example's assembly.yaml and need only PyYAML. The build test copies the example's record
to a temporary project, builds it with cad_model.py run and checks every pair with cad_model.py check, so a
regression in a part's model.py shows as a measured overlap. It needs `uv`; the first run creates the kernel
environments, and their first OCP imports take minutes.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
EXAMPLE_HW = REPO / "examples" / "v6-engine" / "hw"
CLI = HERE.parents[1] / "cad-model" / "scripts" / "cad_model.py"
sys.path.insert(0, str(REPO / "scripts"))
from modload import result_payload  # noqa: E402

CRANK_AXIS = (1.0, 0.0, 0.0)
CYLINDERS = range(1, 7)
FDM_MIN_WALL_MM = 1.2  # the FDM guideline, from the record's process profile and DR-002
FORM_PARTS = ("VSE-0001", "VSE-0002")  # the crankcase window and the piston, both measured below 1.2 mm before the fix
GENERATED = ("versions", "checks", "motion", "renders", "drawings", ".cad", "analysis")


def load_assembly() -> dict:
    return yaml.safe_load((EXAMPLE_HW / "assembly.yaml").read_text(encoding="utf-8"))


def dot(a: list[float], b: tuple[float, ...] | list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class JointAxisTests(unittest.TestCase):
    """The joint frames of the crank train: every axis the slider-crank coupling and the form check rely on."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assembly = load_assembly()
        cls.joints = {j["id"]: j for j in cls.assembly["joints"]}

    def test_the_crank_is_the_one_independent_joint_about_x(self) -> None:
        crank = self.joints["J-001"]
        self.assertEqual((crank["type"], crank["axis"], crank["origin"]), ("revolute", [1, 0, 0], [0, 0, 0]))
        self.assertEqual([j["id"] for j in self.joints.values() if "coupling" not in j and j["type"] == "revolute"], ["J-001"])

    def test_each_piston_slides_along_its_bank_axis(self) -> None:
        for n in CYLINDERS:
            joint = self.joints[f"J-1{n:02d}"]
            with self.subTest(cylinder=n):
                self.assertEqual(joint["type"], "prismatic")
                self.assertAlmostEqual(math.sqrt(dot(joint["axis"], joint["axis"])), 1.0, places=9)
                self.assertAlmostEqual(dot(joint["axis"], CRANK_AXIS), 0.0, places=9)  # perpendicular to the crank axis

    def test_the_banks_form_a_60_degree_v(self) -> None:
        axes = {n: self.joints[f"J-1{n:02d}"]["axis"] for n in CYLINDERS}
        right, left = axes[1], axes[2]
        angle = math.degrees(math.acos(dot(right, left)))
        self.assertAlmostEqual(angle, 60.0, places=6)
        for n in (3, 5):
            self.assertEqual(axes[n], right)
        for n in (4, 6):
            self.assertEqual(axes[n], left)

    def test_each_rod_turns_about_x_through_its_wrist(self) -> None:
        for n in CYLINDERS:
            rod, piston = self.joints[f"J-2{n:02d}"], self.joints[f"J-1{n:02d}"]
            with self.subTest(cylinder=n):
                self.assertEqual((rod["type"], rod["axis"]), ("revolute", [1, 0, 0]))
                self.assertEqual(rod["origin"], piston["origin"])
                self.assertEqual(rod["parent"], f"@piston-{n}")


class BuiltCrankTrainTests(unittest.TestCase):
    """Build the example and check every pair: no piston and rod overlap, no block and crank contact."""

    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("uv"):
            raise unittest.SkipTest("uv is required for the kernel environments")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.hw = Path(cls.tmp.name) / "hw"
        shutil.copytree(EXAMPLE_HW, cls.hw, ignore=shutil.ignore_patterns(*GENERATED))
        cls.run_proc = cls.cli("run", str(cls.hw), "--label", "v1")
        cls.check_proc = cls.cli("check", "v1", "--hw", str(cls.hw), "--no-cache")
        cls.check = result_payload(cls.check_proc.stdout) if cls.check_proc.stdout.strip() else {}
        cls.form_proc = cls.form("--hw", str(cls.hw), "--parts-dir", str(cls.hw / "versions" / "v1" / "parts"), "--fidelity", "F2")
        cls.form_check = result_payload(cls.form_proc.stdout) if cls.form_proc.stdout.strip() else {}

    @staticmethod
    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["uv", "run", "--quiet", "--script", str(CLI), *args],  # no timeout: cold OCP imports take minutes
                              capture_output=True, text=True, check=False)

    @staticmethod
    def form(*args: str) -> subprocess.CompletedProcess[str]:
        script = HERE.parent / "scripts" / "form_check.py"
        return subprocess.run(["uv", "run", "--quiet", "--script", str(script), *args], capture_output=True, text=True, check=False)

    def pairs(self) -> list[dict]:
        found = self.check["checks"]["interference"]  # joint-connected pairs are listed apart
        return [*found["pairs"], *found["joint_pairs"]]

    def test_the_example_builds(self) -> None:
        self.assertEqual(self.run_proc.returncode, 0, self.run_proc.stderr[-2000:])
        self.assertTrue((self.hw / "versions" / "v1" / "assembly.step").is_file())

    def test_no_piston_overlaps_its_rod(self) -> None:
        by_refs = {tuple(p["refs"]): p for p in self.pairs()}
        for n in CYLINDERS:
            pair = by_refs[(f"@piston-{n}", f"@rod-{n}")]
            with self.subTest(cylinder=n):
                self.assertEqual(pair["interference_mm3"], 0.0, f"piston {n} and rod {n} overlap by {pair['interference_mm3']:.2f} mm3")

    def test_the_crank_webs_keep_thrust_clearance_to_the_block(self) -> None:
        pair = next(p for p in self.pairs() if tuple(p["refs"]) == ("VSE-0001", "VSE-0005"))
        self.assertEqual(pair["interference_mm3"], 0.0)
        self.assertGreater(pair["clearance_mm"], 0.0)

    def test_the_crankcase_and_piston_walls_meet_the_fdm_minimum(self) -> None:
        for pn in FORM_PARTS:
            wall = self.form_check["parts"][pn]["min_wall"]
            with self.subTest(part=pn):
                self.assertEqual(wall["required_mm"], FDM_MIN_WALL_MM)
                self.assertGreaterEqual(wall["thickness_mm"], FDM_MIN_WALL_MM, f"{pn} thinnest wall {wall['thickness_mm']} mm at {wall['location_mm']}")

    def test_every_joint_has_a_bore_on_its_axis_in_each_part(self) -> None:
        joints = self.form_check["joints"]
        self.assertEqual(sorted(joints), sorted(j["id"] for j in load_assembly()["joints"]))
        for joint_id, joint in joints.items():
            for part, found in joint["parts"].items():
                with self.subTest(joint=joint_id, part=part):
                    self.assertIsNotNone(found, "no cylinder face on the joint axis")
                    self.assertEqual(found["kind"], "bore")

    def test_the_form_check_reports_no_missing_joint_bore(self) -> None:
        self.assertEqual([f for f in self.form_check["findings"] if f["check"] == "joint_bore_missing"], [], self.form_proc.stderr[-1000:])

    def test_every_pair_check_passes(self) -> None:
        self.assertEqual(self.check["checks"]["interference"]["status"], "pass", json.dumps(self.check["blocking_failures"]))


if __name__ == "__main__":
    unittest.main()
