"""harness/run_tests.sh gives every run its own directory and always leaves that run's `done`.

`uv` is replaced by a stub on PATH, so the tests need no network and no real test file.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "harness" / "run_tests.sh"
# The stub reads the -p pattern of `unittest discover`: test_fail.py fails, test_hang.py blocks.
UV_STUB = """#!/bin/sh
for arg in "$@"; do pattern=$arg; done
: > "$STUB_STARTED"
case $pattern in
  test_fail.py) exit 1 ;;
  test_hang.py) exec sleep 30 ;;
esac
exit 0
"""


class RunTestsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-run-tests-")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "uv"
        stub.write_text(UV_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.root = self.tmp / "runs"
        self.started = self.tmp / "started"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_STARTED": str(self.started),
        }
        self.env.pop("RUN_ROOT", None)
        self.env.pop("RUN_ID", None)

    def start(self, *args: str, **env: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(SCRIPT), "--run-root", str(self.root), *args],
            env={**self.env, **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def run_dir(self, process: subprocess.Popen[str]) -> Path:
        assert process.stdout is not None
        first = process.stdout.readline().strip()
        self.assertTrue(first.startswith("RUN_DIR="), first)
        return Path(first.removeprefix("RUN_DIR="))

    def finish(self, *args: str, **env: str) -> tuple[Path, int, str]:
        process = self.start(*args, **env)
        run_dir = self.run_dir(process)
        out, _ = process.communicate(timeout=60)
        return run_dir, process.returncode, out

    def test_two_runs_get_different_directories(self) -> None:
        first, _, _ = self.finish("pass/test_pass.py")
        second, _, _ = self.finish("pass/test_pass.py")
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root)

    def test_a_passing_run_writes_pid_log_and_rc0(self) -> None:
        run_dir, code, out = self.finish("pass/test_pass.py")
        self.assertEqual(code, 0)
        self.assertEqual((run_dir / "done").read_text(), "rc=0\n")
        self.assertTrue((run_dir / "pid").read_text().strip().isdigit())
        self.assertIn("PASS", out)
        self.assertIn("pass/test_pass.py", (run_dir / "log").read_text())

    def test_an_existing_run_directory_is_refused_and_left_alone(self) -> None:
        run_dir, _, _ = self.finish("pass/test_pass.py", RUN_ID="fixed")
        before = (run_dir / "done").read_text()
        process = self.start("pass/test_fail.py", RUN_ID="fixed")
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 2)
        self.assertIn("refusing", err)
        self.assertEqual((run_dir / "done").read_text(), before)

    def test_a_failing_run_writes_its_own_rc_not_an_earlier_rc0(self) -> None:
        passed, _, _ = self.finish("pass/test_pass.py")
        failed, code, out = self.finish("pass/test_pass.py", "pass/test_fail.py")
        self.assertEqual(code, 1)
        self.assertEqual((failed / "done").read_text(), "rc=1\n")
        self.assertEqual((passed / "done").read_text(), "rc=0\n")
        self.assertRegex(out, r"FAIL \d+s pass/test_fail\.py")

    def test_sigterm_writes_rc143_and_stops_the_test(self) -> None:
        earlier, _, _ = self.finish("pass/test_pass.py")
        self.started.unlink()
        process = self.start("pass/test_hang.py")
        run_dir = self.run_dir(process)
        deadline = time.monotonic() + 30
        while not self.started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.started.exists(), "the stub test never started")
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)
        self.assertEqual(process.returncode, 143)
        self.assertEqual((run_dir / "done").read_text(), "rc=143\n")
        self.assertEqual((earlier / "done").read_text(), "rc=0\n")

    def test_slot_mode_needs_pkg_and_item(self) -> None:
        process = self.start("--slot", "pass/test_pass.py")
        _, err = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 2)
        self.assertIn("--pkg", err)
        self.assertFalse(self.root.exists())

    def test_slot_mode_creates_the_run_directory_under_pkg_and_item(self) -> None:
        home = self.tmp / "home"
        slot = home / "cad-bench" / "bin" / "suite-slot"
        slot.parent.mkdir(parents=True)
        slot.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
        slot.chmod(0o755)
        process = subprocess.Popen(
            [str(SCRIPT), "--slot", "--pkg", "A", "--item", "t1", "pass/test_pass.py"],
            env={**self.env, "HOME": str(home)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        run_dir = self.run_dir(process)
        process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(run_dir.parent, home / "cad-bench" / "pkgA" / "t1")
        self.assertEqual((run_dir / "done").read_text(), "rc=0\n")


if __name__ == "__main__":
    unittest.main()
