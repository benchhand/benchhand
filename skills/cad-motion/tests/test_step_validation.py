"""STEP output carries AP242 geometric validation properties: volume, area and centroid per product."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "fixtures" / "step_header_probe.py"

VALIDATION = re.compile(r"PROPERTY_DEFINITION\('geometric validation property',\s*'([a-z ]+)'")
KINDS = ["centroid", "surface area", "volume"]


@unittest.skipUnless(shutil.which("uv"), "uv is required to write STEP through OCP")
class StepValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def write(self, name: str, *mode: str) -> str:
        out = self.root / name
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(PROBE), str(out), *mode],
                              capture_output=True, text=True, check=False, timeout=900)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return out.read_text(encoding="utf-8")

    def test_part_step_has_validation_entities(self) -> None:
        text = self.write("part.step")
        self.assertEqual(sorted(VALIDATION.findall(text)), KINDS)

    def test_located_part_is_validated_too(self) -> None:
        # a shape that carries a placement is written as an instance of its definition
        text = self.write("located.step", "located")
        self.assertEqual(sorted(VALIDATION.findall(text)), KINDS)

    def test_assembly_step_validates_the_one_definition_and_keeps_its_structure(self) -> None:
        text = self.write("assembly.step", "assembly")
        self.assertEqual(sorted(VALIDATION.findall(text)), KINDS)
        self.assertEqual(len(re.findall(r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(", text)), 2)


if __name__ == "__main__":
    unittest.main()
