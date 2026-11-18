#!/usr/bin/env python3
"""Manufacturing outputs are keyed by what decides them, not by where the skills tree sits (#139).

`cad_model.py run` keys each part's manufacturing outputs in the project's store. Two copies of the same
skills tree in different folders must give the same key, so a version built by the second copy reuses
the outputs the first one stored. An edit to a module the manufacturing code loads must still change the
key. Each test runs the CLI of a copied skills tree on one record version 2 project (CadQuery).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
sys.path.insert(0, str(SKILLS.parent / "scripts"))
from modload import result_payload  # noqa: E402

_spec = importlib.util.spec_from_file_location("cad_model_manufacturing_projects", HERE / "fixtures" / "manufacturing_projects.py")
assert _spec is not None and _spec.loader is not None
projects = sys.modules.get(_spec.name)
if projects is None:
    projects = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = projects
    _spec.loader.exec_module(projects)


def copy_skills(folder: Path) -> Path:
    """A copy of the skills tree in `folder`."""
    return Path(shutil.copytree(SKILLS, folder / "skills", ignore=shutil.ignore_patterns("__pycache__")))


def run(tree: Path, hw: Path, label: str) -> dict[str, Any]:
    """`cad_model.py run` of the copy `tree` for version `label` of `hw`: its JSON result."""
    proc = subprocess.run(["uv", "run", "--quiet", "--script", str(tree / "cad-model" / "scripts" / "cad_model.py"),
                           "run", str(hw), "--label", label], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-3000:]
    return dict(result_payload(proc.stdout))


@unittest.skipUnless(shutil.which("uv"), "uv is required for the kernel environments")
class CheckoutLocationTests(unittest.TestCase):
    """v1 by one copy of the skills tree, then v2 and v3 of the same project by a second copy elsewhere."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        first, second = copy_skills(root / "first"), copy_skills(root / "second" / "elsewhere")
        hw = projects.loose_project(root / "project" / "hw", "cadquery", [])
        cls.runs = {"v1": run(first, hw, "v1"), "v2": run(second, hw, "v2")}
        threemf = second / "cad-model" / "scripts" / "cadmodel" / "threemf.py"
        threemf.write_bytes(threemf.read_bytes() + b"\n# edited\n")
        cls.runs["v3"] = run(second, hw, "v3")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_a_second_copy_of_the_skills_tree_reuses_the_stored_manufacturing_outputs(self) -> None:
        self.assertEqual(self.runs["v1"]["cache"]["manufacturing"], {"built": 1, "reused": 0})
        self.assertEqual(self.runs["v2"]["cache"], {"parts": {"built": 0, "reused": 1},
                                                    "manufacturing": {"built": 0, "reused": 1}})

    def test_an_edit_to_a_module_the_manufacturing_code_loads_rebuilds_its_outputs(self) -> None:
        self.assertEqual(self.runs["v3"]["cache"]["manufacturing"], {"built": 1, "reused": 0})


if __name__ == "__main__":
    unittest.main()
