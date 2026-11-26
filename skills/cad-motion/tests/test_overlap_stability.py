# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The interference common is stable under placement round-off (benchhand-013).

A heat-set insert flush with the counterbore floor of its collar measured 0 mm3 at one mirror-image
placement, 150.7 mm3 (the whole insert) in an earlier collar, and 39.4 mm3 at the other: the
round-off of the placements, about 3e-7 mm, put the designed coincidences a few Boolean fuzzy values
apart.

Run: python3 -m unittest discover -s skills/cad-motion/tests -p test_overlap_stability.py -v
Needs uv, as the STEP tests do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "fixtures" / "overlap_probe.py"
FIXTURES = HERE / "fixtures" / "benchhand013"
RING_MM3 = 39.392  # the insert ring inside the collar wall, as Cut and fuzzy 1e-6 to 1e-4 agree
SLACK_MM3 = 1e-3   # INTERFERENCE_MM3: readings may differ by kernel noise, never by a verdict


@unittest.skipUnless(shutil.which("uv"), "uv is required to run the OCP probe")
class OverlapStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(PROBE), str(FIXTURES)],
                              capture_output=True, text=True, check=False, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(f"overlap probe failed: {proc.stderr[-2000:]}")
        cls.readings = json.loads(proc.stdout)

    def test_mirror_image_placements_measure_equal_volumes(self) -> None:
        m1, m2 = self.readings["m1"][0][1], self.readings["m2"][0][1]
        self.assertAlmostEqual(m1, m2, delta=SLACK_MM3)
        self.assertAlmostEqual(m1, RING_MM3, delta=SLACK_MM3)

    def test_sub_micron_offsets_do_not_change_the_volume(self) -> None:
        for name, rows in self.readings.items():
            for index, (_, volume) in enumerate(rows):
                with self.subTest(placement=name, reading=index):
                    self.assertAlmostEqual(volume, RING_MM3, delta=SLACK_MM3)

    def test_repeated_readings_are_bitwise_identical(self) -> None:
        for name, rows in self.readings.items():
            for index in range(0, len(rows), 2):
                with self.subTest(placement=name, reading=index):
                    self.assertEqual(rows[index], rows[index + 1])


if __name__ == "__main__":
    unittest.main()
