# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The overlap depth of a stop uses the interference fuzzy value, not the finer one (benchhand-013).

The heat-set insert flush with its collar's counterbore floor, at the two mirror-image placements of
cad-motion's fixtures, shares a ring with the collar wall. Under the fuzzy value 1e-7 the Common kept none
of it at one placement (depth -2.0, the empty-shape value) and all of the insert at the other (6.3 mm);
the ring is 39.4 mm3 at both.

Run: python3 -m unittest discover -s skills/cad-analysis/tests -p test_stop_overlap_depth.py -v
Needs uv, as the STEP tests do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "fixtures" / "overlap_depth_probe.py"
FIXTURES = HERE.parents[1] / "cad-motion" / "tests" / "fixtures" / "benchhand013"


@unittest.skipUnless(shutil.which("uv"), "uv is required to run the OCP probe")
class OverlapDepthStabilityTests(unittest.TestCase):
    def test_mirror_image_placements_measure_the_same_depth(self) -> None:
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(PROBE), str(FIXTURES)],
                              capture_output=True, text=True, check=False, timeout=900)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        depths = json.loads(proc.stdout)
        self.assertGreater(depths["m1"], 0.0)
        self.assertAlmostEqual(depths["m1"], depths["m2"], delta=1e-3)


if __name__ == "__main__":
    unittest.main()
