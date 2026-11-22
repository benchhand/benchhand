#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Every cad-motion entry point refuses a record of another record_version: exit 1, record.version_unsupported
on stderr, no traceback, no geometry work and nothing written. Each test builds its own project: no case shares
a folder with another.

Run: python3 -m unittest discover -s cad/skills/cad-motion/tests -p test_version_gate.py -v
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
SKILLS = HERE.parents[1]
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(SCRIPTS))
import motion_version  # noqa: E402 - the kernel-free sealed version
from modload import load_module as load  # noqa: E402

pose = load("cad_motion_pose", SCRIPTS / "pose.py")
sweep = load("cad_motion_sweep", SCRIPTS / "sweep.py")
play = load("cad_motion_play", SCRIPTS / "play.py")
access = load("cad_motion_access", SCRIPTS / "access.py")
merge_sweeps = load("cad_motion_merge_sweeps", SCRIPTS / "merge_sweeps.py")
spot_check = load("cad_motion_spot_check", SCRIPTS / "spot_check.py")
pair_policy = load("cad_motion_pair_policy", SCRIPTS / "pair_policy.py")
intent = load("cad_record_manufacturing_intent", SKILLS / "cad-record" / "scripts" / "manufacturing_intent.py")
design_rules = load("cad_record_design_rules", SKILLS / "cad-record" / "scripts" / "design_rules.py")


def run(entry: Callable[[list[str]], int], argv: list[str]) -> tuple[Any, str, str]:
    """(exit code, stdout, stderr) of one in-process run of `entry`."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = entry(argv)
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def files_below(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


class Project(unittest.TestCase):
    """A sealed version whose record names `record_version`."""

    record_version: Any = 2

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hw = self.tmp / "project" / "hw"
        motion_version.write(self.hw, pose, record_version=self.record_version)
        self.before = files_below(self.hw.parent)

    def assertRefused(self, result: tuple[Any, str, str]) -> None:
        code, out, err = result
        self.assertEqual(code, 1, f"stdout={out!r} stderr={err!r}")
        self.assertIn("record.version_unsupported", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(files_below(self.hw.parent), self.before, "a refused record writes nothing")


class VersionedEntryPoints(Project):
    def test_pose_refuses_a_record_of_version_2(self) -> None:
        self.assertRefused(run(pose.main, ["--hw", str(self.hw), "--version", "v1"]))

    def test_sweep_refuses_a_record_of_version_2(self) -> None:
        self.assertRefused(run(sweep.main, ["--hw", str(self.hw), "--version", "v1"]))

    def test_play_refuses_a_record_of_version_2(self) -> None:
        self.assertRefused(run(play.main, ["--hw", str(self.hw), "--version", "v1"]))

    def test_access_refuses_a_record_of_version_2(self) -> None:
        self.assertRefused(run(access.main, ["--hw", str(self.hw), "--version", "v1"]))

    def test_merge_sweeps_refuses_a_record_of_version_2(self) -> None:
        self.assertRefused(run(merge_sweeps.main, ["--hw", str(self.hw), "--version", "v1", "--case", "merged",
                                                   "--inputs", str(self.tmp / "a"), str(self.tmp / "b")]))

    def test_spot_check_refuses_a_record_of_version_2(self) -> None:
        self.assertRefused(run(spot_check.main, ["--hw", str(self.hw), "--version", "v1",
                                                 "--result", str(self.tmp / "a")]))


class ScratchEntryPoints(Project):
    """A run on loose parts reads the record in hw/ itself."""

    def test_pose_refuses_a_scratch_run_on_a_record_of_version_2(self) -> None:
        out = self.hw.parent / ".cad-work" / "posed.step"
        self.assertRefused(run(pose.main, ["--hw", str(self.hw), "--parts-dir", str(self.tmp), "--out", str(out)]))

    def test_play_refuses_a_scratch_run_on_a_record_of_version_2(self) -> None:
        self.assertRefused(run(play.main, ["--hw", str(self.hw), "--parts-dir", str(self.tmp)]))

    def test_access_refuses_a_scratch_run_on_a_record_of_version_2(self) -> None:
        self.assertRefused(run(access.main, ["--hw", str(self.hw), "--parts-dir", str(self.tmp)]))


class RecordReaders(Project):
    """The readers of the record's own files, which no entry point reaches before another reader."""

    def test_load_assembly_refuses_a_record_of_version_2(self) -> None:
        with self.assertRaises(intent.UnsupportedRecordVersion):
            pose.load_assembly(self.hw)

    def test_the_interface_reader_refuses_a_record_of_version_2(self) -> None:
        with self.assertRaises(intent.UnsupportedRecordVersion):
            pair_policy.read_interfaces(self.hw)

    def test_the_clearance_margin_of_a_record_of_version_2_is_not_read(self) -> None:
        with self.assertRaises(intent.UnsupportedRecordVersion):
            design_rules.load_clearance_margins(self.hw)


class NoVersion(Project):
    record_version = None

    def test_a_record_without_a_version_is_refused_like_another_version(self) -> None:
        self.assertRefused(run(sweep.main, ["--hw", str(self.hw), "--version", "v1"]))


class CurrentRecord(Project):
    record_version = 1

    def test_a_record_of_version_1_still_loads_its_assembly(self) -> None:
        self.assertEqual(len(pose.load_assembly(self.hw).joints), 3)


if __name__ == "__main__":
    unittest.main()
