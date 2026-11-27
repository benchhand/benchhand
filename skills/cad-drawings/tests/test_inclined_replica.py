#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Frame-aligned drawings stay as they are, and inclined features get their own named findings (design T8, T10).

The units that motivate this (GHA-0001, VSE-0005) are not vendored. ``fixtures/inclined_replicas.py`` generates
synthetic replicas of their shapes, built through ``cad_model.py`` and drawn through the drawing CLI. One opt-in
test draws the real units from a project path instead: set CAD_REAL_UNITS and CAD_REAL_UNITS_PARTS.

Run: uv run --no-project --with-requirements harness/test-requirements.txt python -B -m unittest discover -s skills/cad-drawings/tests -p test_inclined_replica.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "fixtures"))
import golden_rows  # noqa: E402
import inclined_replicas  # noqa: E402
import test_part_drawings as base  # noqa: E402  (the module, not its classes: unittest would collect them here too)


@unittest.skipUnless(base.FREECAD_PRESENT and base.UV, "needs FreeCAD and uv")
class Replicas(unittest.TestCase):
    """Each replica is a migrated copy of the lamp-arm template whose LMP-0005 is the replica; built once."""

    units: dict[str, Path] = {}
    tmp: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="cad-replica-")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def unit(self, name: str, replica: tuple[str, str]) -> Path:
        if name not in self.units:
            hw = Path(self.tmp.name) / name / "hw"
            base.PinDrawingGate.fx().migrated_copy(base.TEMPLATE, hw)
            inclined_replicas.install(hw, "LMP-0005", replica)
            build = base.run(base.UV, "run", "--script", str(base.CAD_MODEL), "run", str(hw), "--label", "v1",
                             "--kernel", "auto")
            self.assertEqual(build.returncode, 0, build.stdout[-2000:] + build.stderr[-2000:])
            self.units[name] = hw
        return self.units[name]

    def draw(self, name: str, replica: tuple[str, str]) -> tuple[int, dict]:
        hw = self.unit(name, replica)
        proc = base.run(base.UV, "run", "--script", str(base.DRAWING), str(hw), "--version", "v1", "--part", "LMP-0005",
                        "--output-dir", str(hw.parent / ".cad-work" / "out"), timeout=900)
        try:
            return proc.returncode, json.loads(proc.stdout)["parts"][0]
        except (json.JSONDecodeError, KeyError) as exc:
            raise AssertionError(f"drawing CLI gave no result (exit {proc.returncode}): {proc.stderr[-3000:]}") from exc

    def test_the_v3_collar_with_controlled_model_inserts_keeps_its_rows(self) -> None:
        code, result = self.draw("collar_v3", inclined_replicas.collar("surface"))
        self.assertEqual((code, result["status"], result["findings"]), (0, "pass", []))
        golden_rows.check(self, "collar_v3", result)

    def test_a_frame_aligned_crank_keeps_its_rows(self) -> None:
        code, result = self.draw("crank", inclined_replicas.crank())
        self.assertEqual((code, result["status"], result["findings"]), (0, "pass", []))
        golden_rows.check(self, "crank", result)

    def test_an_inclined_bore_and_boss_face_each_get_their_one_named_finding(self) -> None:
        """Named as native features, the inclined bores and boss faces are unlocated: one finding each, from the
        handler, never a second from the closure check and never a crash."""
        code, result = self.draw("collar_native", inclined_replicas.collar("native"))
        self.assertEqual((code, result["status"]), (1, "fail"), result)
        for side in ("plus", "minus"):
            bore = [f for f in result["findings"] if f.get("feature") == f"insert_bore_{side}"]
            self.assertEqual([(f["code"], f["message"]) for f in bore],
                             [("dimension_coverage_incomplete", f"insert_bore_{side}: its axis is not parallel to a frame axis")])
            boss = [f for f in result["findings"] if f.get("feature") == f"boss_face_{side}"]
            self.assertEqual([f["code"] for f in boss], ["dimension_coverage_incomplete"], boss)
            self.assertIn("not normal to a frame axis", boss[0]["message"])

REAL_UNITS = os.environ.get("CAD_REAL_UNITS")
REAL_PARTS = os.environ.get("CAD_REAL_UNITS_PARTS", "")


@unittest.skipUnless(REAL_UNITS and REAL_PARTS and base.FREECAD_PRESENT and base.UV,
                     "opt-in: set CAD_REAL_UNITS to a project's hw path and CAD_REAL_UNITS_PARTS to 'PN:label ...' "
                     "(for example 'GHA-0001:v3 VSE-0005:v6'); needs FreeCAD and uv")
class RealUnits(unittest.TestCase):
    """The real units are drawn in place, read only: the output goes to a scratch folder, never into the project.

    Their rows are not goldens (the units move); what must hold is that the tool answers with a result and named
    findings, never a traceback or exit 2."""

    def test_each_named_unit_is_drawn_without_a_crash_or_an_unnamed_finding(self) -> None:
        for spec in REAL_PARTS.split():
            pn, label = spec.split(":")
            with self.subTest(unit=spec), tempfile.TemporaryDirectory(prefix="cad-real-unit-") as scratch:
                proc = base.run(base.UV, "run", "--script", str(base.DRAWING), str(REAL_UNITS), "--version", label,
                                "--part", pn, "--output-dir", str(Path(scratch) / ".cad-work" / "out"), timeout=1800)
                self.assertIn(proc.returncode, (0, 1), proc.stderr[-3000:])
                self.assertNotIn("Traceback", proc.stderr)
                result = json.loads(proc.stdout)["parts"][0]
                self.assertIn(result["status"], ("pass", "fail"), result)
                for item in result.get("findings", []):
                    self.assertTrue(item.get("code"), item)


if __name__ == "__main__":
    unittest.main()
