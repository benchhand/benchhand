"""The STEP FILE_NAME time is a function of SOURCE_DATE_EPOCH, never of the clock."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
PROBE = HERE / "fixtures" / "step_header_probe.py"

sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from modload import load_module as load  # noqa: E402

pose = load("cad_motion_pose", SCRIPTS / "pose.py")

FILE_NAME = re.compile(rb"FILE_NAME\('[^']*','([^']*)'")


class StepEpochTests(unittest.TestCase):
    def test_unset_is_a_fixed_date(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("SOURCE_DATE_EPOCH", None)
            self.assertEqual(pose.step_timestamp(), "1980-01-01T00:00:00")

    def test_epoch_is_read_as_utc(self) -> None:
        # 1700000000 = 2023-11-14T22:13:20Z
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            self.assertEqual(pose.step_timestamp(), "2023-11-14T22:13:20")

    def test_bounds_are_inclusive(self) -> None:
        for value, expected in (("0", "1970-01-01T00:00:00"), ("253402300799", "9999-12-31T23:59:59")):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": value}):
                self.assertEqual(pose.step_timestamp(), expected)

    def test_bad_values_are_rejected(self) -> None:
        for value in ("", "-1", "1.5", " 7", "1e9", "+5", "\u0661\u0662", "253402300800", "9" * 5000):
            with self.subTest(value=value[:12]), mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": value}):
                with self.assertRaisesRegex(pose.MotionError, "SOURCE_DATE_EPOCH"):
                    pose.step_timestamp()


@unittest.skipUnless(shutil.which("uv"), "uv is required to write STEP through OCP")
class StepHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def write(self, name: str, epoch: str | None) -> bytes:
        env = {key: value for key, value in os.environ.items() if key != "SOURCE_DATE_EPOCH"}
        if epoch is not None:
            env["SOURCE_DATE_EPOCH"] = epoch
        out = self.root / name
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(PROBE), str(out)], env=env,
                              capture_output=True, text=True, check=False, timeout=900)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return out.read_bytes()

    def test_two_writes_are_byte_identical_across_clock_seconds(self) -> None:
        first = self.write("a.step", None)
        time.sleep(1.1)   # the wall clock OCCT would stamp has moved on
        second = self.write("b.step", None)
        self.assertEqual(first, second)
        self.assertEqual(FILE_NAME.search(first).group(1), b"1980-01-01T00:00:00")

    def test_epoch_changes_only_the_timestamp(self) -> None:
        early = self.write("early.step", "1700000000")
        late = self.write("late.step", "1700003600")
        self.assertEqual(FILE_NAME.search(early).group(1), b"2023-11-14T22:13:20")
        self.assertEqual(FILE_NAME.search(late).group(1), b"2023-11-14T23:13:20")
        self.assertEqual(FILE_NAME.sub(b"", early), FILE_NAME.sub(b"", late))
        self.assertEqual(len(early), len(late))

    def test_bad_epoch_writes_nothing(self) -> None:
        env = {**os.environ, "SOURCE_DATE_EPOCH": "tomorrow"}
        out = self.root / "bad.step"
        proc = subprocess.run(["uv", "run", "--quiet", "--script", str(PROBE), str(out)], env=env,
                              capture_output=True, text=True, check=False, timeout=900)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SOURCE_DATE_EPOCH", proc.stderr)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
