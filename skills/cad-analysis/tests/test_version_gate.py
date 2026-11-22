#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""cad-analysis entry points refuse a record of another record_version: exit 1, record.version_unsupported on
stderr, no traceback, no packet and nothing written. Each test builds its own project.

Run: python3 -m unittest discover -s cad/skills/cad-analysis/tests -p test_version_gate.py -v
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
SCRIPTS = SKILLS / "cad-analysis" / "scripts"
MOTION = SKILLS / "cad-motion"
sys.path.insert(0, str(MOTION / "tests" / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(SCRIPTS))
import motion_version  # noqa: E402 - the kernel-free sealed version of cad-motion's tests
from modload import load_module as load  # noqa: E402

pose = load("cad_motion_pose", MOTION / "scripts" / "pose.py")
stop_reaction = load("cad_analysis_stop_reaction", SCRIPTS / "stop_reaction.py")
fea_conditions = load("cad_analysis_fea_conditions", SCRIPTS / "fea_conditions.py")


def run(argv: list[str]) -> tuple[Any, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = stop_reaction.main(argv)
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def files_below(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


class Project(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hw = self.tmp / "project" / "hw"
        motion_version.write(self.hw, pose, record_version=2)
        self.before = files_below(self.hw.parent)

    def assertRefused(self, result: tuple[Any, str, str]) -> None:
        code, out, err = result
        self.assertEqual(code, 1, f"stdout={out!r} stderr={err!r}")
        self.assertIn("record.version_unsupported", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "", "no packet is emitted for a refused record")
        self.assertEqual(files_below(self.hw.parent), self.before, "a refused record writes nothing")


class StopReaction(Project):
    def test_a_version_of_record_version_2_is_refused(self) -> None:
        self.assertRefused(run(["--hw", str(self.hw), "--version", "v1"]))

    def test_a_scratch_run_on_a_record_of_version_2_is_refused(self) -> None:
        self.assertRefused(run(["--hw", str(self.hw), "--parts-dir", str(self.tmp)]))


class Conditions(Project):
    def test_the_record_reader_of_the_fea_conditions_refuses_version_2(self) -> None:
        with self.assertRaises(fea_conditions.intent().UnsupportedRecordVersion):
            fea_conditions.read_record(self.hw)


if __name__ == "__main__":
    unittest.main()
