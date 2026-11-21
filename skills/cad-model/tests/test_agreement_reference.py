#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""scripts/agreement_timing.py: the fixed reference pair that the agreement timing runs on is the same on every run.

A change to the generator (seed, grids, radii, noise) moves the digest or the counts here, so a timing taken before
and after it is never compared by mistake. The expected values below were taken from the script's first run.

Run: python3 -m unittest discover -s skills/cad-model/tests -p test_agreement_reference.py -v
Needs `uv`; the script's environment holds only numpy.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "agreement_timing.py"
DIGEST = "2e8f5f263629e8cddd3fec41e81e891da3a71ca2454b349e6a9361452bd1d1af"


class AgreementReferenceTests(unittest.TestCase):
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(SCRIPT), "--runs", "1", "--json"],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr[-2000:]
        cls.result = json.loads(proc.stdout)

    def test_the_reference_has_thousands_of_triangles(self) -> None:
        """A 96 x 48 quad grid is 2 x 4608 triangles, a 128 x 64 grid is 2 x 8192."""
        self.assertEqual(self.result["glb_triangles"], 9216)
        self.assertEqual(self.result["step_triangles"], 16384)

    def test_the_reference_arrays_are_unchanged(self) -> None:
        self.assertEqual(self.result["seed"], 20260429)
        self.assertEqual(self.result["digest"], DIGEST)

    def test_compare_agrees_on_the_reference(self) -> None:
        self.assertEqual(self.result["status"], "pass")


if __name__ == "__main__":
    unittest.main()
