"""harness/run_tests.sh gives every run its own directory and always leaves that run's `done`.

`uv` and `suite-slot` are replaced by stubs, so the tests need no network and no real test file.
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
echo "$@" >> "$STUB_ARGS"
for arg in "$@"; do pattern=$arg; done
echo $$ > "$STUB_PID"
: > "$STUB_STARTED"
case $pattern in
  test_fail.py) exit 1 ;;
  test_hang.py) exec sleep 5 ;;
esac
exit 0
"""
# A stand-in for ~/cad-bench/bin/suite-slot: FAKE_SLOT_MODE picks how it behaves before the command.
SLOT_STUB = """#!/bin/sh
case $FAKE_SLOT_MODE in
  queue) echo $$ > "$FAKE_SLOT_PID"; : > "$STUB_STARTED"; exec sleep 5 ;;
  fail) exit 1 ;;
  breaklog) while [ "$1" != --run-dir ]; do shift; done; mkdir "$2/log" ;;
esac
exec "$@"
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class RunTestsCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cad-run-tests-")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "uv").write_text(UV_STUB, encoding="utf-8")
        (bin_dir / "uv").chmod(0o755)
        self.home = self.tmp / "home"
        slot = self.home / "cad-bench" / "bin" / "suite-slot"
        slot.parent.mkdir(parents=True)
        slot.write_text(SLOT_STUB, encoding="utf-8")
        slot.chmod(0o755)
        self.root = self.tmp / "runs"
        self.started = self.tmp / "started"
        self.stub_pid = self.tmp / "stub.pid"
        self.slot_pid = self.tmp / "slot.pid"
        self.stub_args = self.tmp / "args"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
            "STUB_STARTED": str(self.started),
            "STUB_PID": str(self.stub_pid),
            "STUB_ARGS": str(self.stub_args),
            "FAKE_SLOT_PID": str(self.slot_pid),
        }
        for name in ("RUN_ROOT", "RUN_ID", "PKG", "ITEM"):
            self.env.pop(name, None)

    def start(self, *args: str, **env: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(SCRIPT), *args],
            env={**self.env, **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def local(self, *args: str, **env: str) -> subprocess.Popen[str]:
        return self.start("--run-root", str(self.root), *args, **env)

    def slotted(self, *args: str, **env: str) -> subprocess.Popen[str]:
        return self.start("--slot", "--pkg", "A", "--item", "t1", *args, **env)

    def run_dir(self, process: subprocess.Popen[str]) -> Path:
        assert process.stdout is not None
        first = process.stdout.readline().strip()
        self.assertTrue(first.startswith("RUN_DIR="), first)
        return Path(first.removeprefix("RUN_DIR="))

    def finish(self, process: subprocess.Popen[str]) -> tuple[Path, int, str, str]:
        run_dir = self.run_dir(process)
        out, err = process.communicate(timeout=60)
        return run_dir, process.returncode, out, err

    def wait_for_start(self) -> None:
        deadline = time.monotonic() + 30
        while not self.started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.started.exists(), "the stub never started")

    def kill_when_done(self, pid_file: Path) -> int:
        pid = int(pid_file.read_text())

        def cleanup() -> None:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)

        self.addCleanup(cleanup)
        return pid

    def terminate(self, process: subprocess.Popen[str]) -> None:
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)


class DirectoryTests(RunTestsCase):
    def test_two_runs_get_different_directories(self) -> None:
        first, *_ = self.finish(self.local("pass/test_pass.py"))
        second, *_ = self.finish(self.local("pass/test_pass.py"))
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root)

    def test_a_passing_run_writes_pid_log_and_rc0(self) -> None:
        run_dir, code, out, _ = self.finish(self.local("pass/test_pass.py"))
        self.assertEqual(code, 0)
        self.assertEqual((run_dir / "done").read_text(), "rc=0\n")
        self.assertTrue((run_dir / "pid").read_text().strip().isdigit())
        self.assertIn("PASS", out)
        self.assertIn("pass/test_pass.py", (run_dir / "log").read_text())

    def test_an_existing_run_directory_is_refused_and_left_alone(self) -> None:
        run_dir, *_ = self.finish(self.local("pass/test_pass.py", RUN_ID="fixed"))
        before = (run_dir / "done").read_text()
        process = self.local("pass/test_fail.py", RUN_ID="fixed")
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 2)
        self.assertIn("refusing", err)
        self.assertEqual((run_dir / "done").read_text(), before)

    def test_harness_and_script_tests_use_the_minimal_pins_and_skill_tests_the_full(self) -> None:
        self.finish(
            self.local("harness/test_x.py", "scripts/test_y.py", "skills/cad-fits/test_z.py")
        )
        lines = self.stub_args.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("harness/test-requirements-min.txt", lines[0])
        self.assertIn("harness/test-requirements-min.txt", lines[1])
        self.assertIn("harness/test-requirements.txt", lines[2])
        self.assertNotIn("-min", lines[2])


class DoneMarkerTests(RunTestsCase):
    def test_a_failing_run_writes_its_own_rc_not_an_earlier_rc0(self) -> None:
        passed, *_ = self.finish(self.local("pass/test_pass.py"))
        failed, code, out, _ = self.finish(self.local("pass/test_pass.py", "pass/test_fail.py"))
        self.assertEqual(code, 1)
        self.assertEqual((failed / "done").read_text(), "rc=1\n")
        self.assertEqual((passed / "done").read_text(), "rc=0\n")
        self.assertRegex(out, r"FAIL \d+s pass/test_fail\.py")

    def test_sigterm_writes_rc143_and_the_test_process_is_gone(self) -> None:
        earlier, *_ = self.finish(self.local("pass/test_pass.py"))
        self.started.unlink()
        process = self.local("pass/test_hang.py")
        run_dir = self.run_dir(process)
        self.wait_for_start()
        pid = self.kill_when_done(self.stub_pid)
        self.assertTrue(alive(pid))
        self.terminate(process)
        self.assertEqual(process.returncode, 143)
        self.assertEqual((run_dir / "done").read_text(), "rc=143\n")
        self.assertEqual((earlier / "done").read_text(), "rc=0\n")
        self.assertFalse(alive(pid), "the test process outlived the runner")


class SlotTests(RunTestsCase):
    def test_slot_mode_needs_pkg_and_item(self) -> None:
        process = self.start("--slot", "pass/test_pass.py")
        _, err = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 2)
        self.assertIn("--pkg", err)

    def test_slot_mode_runs_under_the_slot_in_a_pkg_item_run_directory(self) -> None:
        run_dir, code, _, _ = self.finish(self.slotted("pass/test_pass.py"))
        self.assertEqual(code, 0)
        self.assertEqual(run_dir.parent, self.home / "cad-bench" / "pkgA" / "t1")
        self.assertEqual((run_dir / "done").read_text(), "rc=0\n")
        self.assertIn("PASS", (run_dir / "log").read_text())
        self.assertTrue((run_dir / "pid").exists())

    def test_sigterm_while_queued_writes_rc143_and_stops_the_slot(self) -> None:
        process = self.slotted("pass/test_pass.py", FAKE_SLOT_MODE="queue")
        run_dir = self.run_dir(process)
        self.wait_for_start()
        pid = self.kill_when_done(self.slot_pid)
        self.terminate(process)
        self.assertEqual(process.returncode, 143)
        self.assertEqual((run_dir / "done").read_text(), "rc=143\n")
        self.assertFalse(alive(pid), "the queued slot outlived the runner")

    def test_a_slot_that_exits_without_running_the_command_still_gets_done(self) -> None:
        run_dir, code, _, _ = self.finish(self.slotted("pass/test_pass.py", FAKE_SLOT_MODE="fail"))
        self.assertEqual(code, 1)
        self.assertEqual((run_dir / "done").read_text(), "rc=1\n")

    def test_a_metadata_write_failure_in_the_worker_fails_the_run(self) -> None:
        run_dir, code, _, _ = self.finish(
            self.slotted("pass/test_pass.py", FAKE_SLOT_MODE="breaklog")
        )
        self.assertNotEqual(code, 0)
        self.assertNotEqual((run_dir / "done").read_text(), "rc=0\n")
        self.assertTrue((run_dir / "done").read_text().startswith("rc="))
        self.assertFalse(self.stub_args.exists(), "a test ran after its log failed")


class HandoffTests(RunTestsCase):
    def handoff(self, run_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), "--run-dir", str(run_dir), "pass/test_pass.py"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_a_handed_off_run_directory_can_be_claimed_once(self) -> None:
        run_dir = self.tmp / "handoff"
        run_dir.mkdir()
        (run_dir / "pid").write_text("1\n", encoding="utf-8")
        first = self.handoff(run_dir)
        log = (run_dir / "log").read_text()
        second = self.handoff(run_dir)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 2)
        self.assertIn("refusing", second.stderr)
        self.assertEqual((run_dir / "log").read_text(), log)

    def test_a_handoff_never_writes_the_done_marker(self) -> None:
        run_dir = self.tmp / "handoff"
        run_dir.mkdir()
        self.handoff(run_dir)
        self.assertFalse((run_dir / "done").exists())

    def test_a_handoff_to_a_finished_run_is_refused(self) -> None:
        run_dir = self.tmp / "handoff"
        run_dir.mkdir()
        (run_dir / "done").write_text("rc=0\n", encoding="utf-8")
        self.assertEqual(self.handoff(run_dir).returncode, 2)
        self.assertFalse((run_dir / "claimed").exists())


if __name__ == "__main__":
    unittest.main()
