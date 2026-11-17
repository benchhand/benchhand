#!/usr/bin/env python3
"""cadmodel.agreement: whether a GLB mesh depicts the STEP shape it is placed for, from the probe in
fixtures/agreement_probe.py (it builds the shapes and the GLB with OCP; this file judges the verdicts).

Run: python3 -m unittest discover -s skills/cad-model/tests -p test_agreement.py -v
Needs `uv`; the probe's environment holds only OCP and pyyaml.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

PROBE = Path(__file__).resolve().parent / "fixtures" / "agreement_probe.py"


class AgreementTests(unittest.TestCase):
    probe: dict

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.run(["uv", "run", "--quiet", "--script", str(PROBE), folder],
                                  capture_output=True, text=True, check=False)  # cold OCP imports take minutes
        assert proc.returncode == 0, proc.stderr[-2000:]
        cls.probe = json.loads(proc.stdout)

    def status(self, name: str) -> str:
        return self.probe["cases"][name]["status"]

    def test_a_mesh_of_the_same_shape_agrees(self) -> None:
        self.assertEqual(self.status("same_shape"), "pass", self.probe["cases"]["same_shape"])

    def test_a_cylinder_of_the_blocks_volume_does_not_agree(self) -> None:
        self.assertEqual(self.status("equal_volume_cylinder"), "fail")

    def test_a_mesh_three_millimetres_off_does_not_agree(self) -> None:
        self.assertEqual(self.status("shifted_3_mm"), "fail")

    def test_a_hole_eight_millimetres_off_does_not_agree(self) -> None:
        self.assertEqual(self.status("hole_moved_8_mm"), "fail")

    def test_a_mesh_lacking_the_steps_hole_does_not_agree(self) -> None:
        row = self.probe["cases"]["hole_missing_in_mesh"]
        self.assertEqual(row["status"], "fail")
        self.assertGreater(row["step_to_glb_mm"], row["tolerance_mm"])  # the STEP's hole wall has no mesh near it

    def test_a_mesh_with_a_hole_the_step_lacks_does_not_agree(self) -> None:
        row = self.probe["cases"]["extra_hole_in_mesh"]
        self.assertEqual(row["status"], "fail")
        self.assertGreater(row["glb_to_step_mm"], row["tolerance_mm"])  # the mesh's hole wall is off the STEP's surface

    def test_a_hole_a_quarter_millimetre_too_wide_does_not_agree(self) -> None:
        self.assertEqual(self.status("hole_6_vs_6_5"), "fail")

    def test_a_mesh_a_thousand_times_too_large_does_not_agree(self) -> None:
        self.assertEqual(self.status("thousand_times_larger"), "fail")

    def test_an_empty_mesh_or_shape_is_unknown_not_a_pass(self) -> None:
        self.assertEqual((self.status("mesh_empty"), self.status("step_empty")), ("unknown", "unknown"))

    def test_what_the_data_cannot_judge_is_unknown_with_its_reason(self) -> None:
        rows = self.probe["abstentions"]["rows"]
        self.assertEqual(rows["no_node"]["status"], "unknown")
        self.assertIn("no mesh node", rows["no_node"]["reason"])
        self.assertEqual(rows["no_shape"]["status"], "unknown")
        self.assertIn("no shape for Y", rows["no_shape"]["reason"])
        self.assertEqual(rows["has_node"]["status"], "fail")  # the one-triangle GLB is not the block: judged, not skipped
        self.assertEqual(set(self.probe["abstentions"]["no_deflection"].values()), {"unknown"})

    def test_a_nested_glb_node_reads_in_millimetres_z_up_at_its_world_pose(self) -> None:
        glb = self.probe["glb"]
        self.assertEqual(glb["names"], ["inner"])  # only a node that holds a mesh reads as one
        # glTF meters, Y up -> CAD mm, Z up: (x, y, z) -> (1000 x, -1000 z, 1000 y). The child's local points
        # (0,0,0), (.001,0,0), (0,.001,0) turn +90 degrees about Y to (0,0,0), (0,0,-.001), (0,.001,0), then add
        # the parent's (.01, .02, .03): (10, -30, 20), (10, -29, 20), (10, -30, 21).
        want = [[10.0, -30.0, 20.0], [10.0, -29.0, 20.0], [10.0, -30.0, 21.0]]
        for got_point, want_point in zip(glb["points"], want):
            for got, expected in zip(got_point, want_point):
                self.assertAlmostEqual(got, expected, places=4)
        self.assertEqual(glb["triangles"], [[0, 1, 2]])


if __name__ == "__main__":
    unittest.main()
