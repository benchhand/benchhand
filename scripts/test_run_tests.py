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

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harness" / "run_tests.sh"
# The stub reads the -p pattern of `unittest discover` (its last argument) to pick a behavior.
UV_STUB = """#!/bin/sh
echo "$@" >> "$STUB_ARGS"
for arg in "$@"; do pattern=$arg; done
echo $$ > "$STUB_PID"
: > "$STUB_STARTED"
case $pattern in
  test_fail.py) exit 1 ;;
  test_hang.py) exec sleep 600 > /dev/null 2>&1 ;;
  test_stubborn.py)
    # a TERM-ignoring test process with a TERM-ignoring grandchild
    trap '' TERM
    sh -c 'trap "" TERM; echo $$ > "$STUB_GRANDCHILD"; exec sleep 600' > /dev/null 2>&1 &
    sleep 600 > /dev/null 2>&1 ;;
  test_breaklog.py)
    for pid_file in "$STUB_RUN_ROOT"/*/pid; do run_dir=${pid_file%/pid}; done
    rm -f "$run_dir/log"; mkdir "$run_dir/log" ;;
esac
exit 0
"""
# A stand-in for ~/cad-bench/bin/suite-slot: FAKE_SLOT_MODE picks how it behaves before the command.
SLOT_STUB = """#!/bin/sh
case $FAKE_SLOT_MODE in
  queue) echo $$ > "$FAKE_SLOT_PID"; : > "$STUB_STARTED"; exec sleep 600 > /dev/null 2>&1 ;;
  fail) exit 1 ;;
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
        self.grandchild_pid = self.tmp / "grandchild.pid"
        self.stub_args = self.tmp / "args"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
            "STUB_STARTED": str(self.started),
            "STUB_PID": str(self.stub_pid),
            "STUB_ARGS": str(self.stub_args),
            "STUB_GRANDCHILD": str(self.grandchild_pid),
            "STUB_RUN_ROOT": str(self.root),
            "FAKE_SLOT_PID": str(self.slot_pid),
            "RUN_TESTS_GRACE": "1",
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

    def wait_for(self, path: Path) -> None:
        deadline = time.monotonic() + 30
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(path.exists(), f"{path.name} never appeared")

    def kill_at_cleanup(self, pid_file: Path) -> int:
        """The pid in pid_file, killed when the test ends if it is still alive."""
        self.wait_for(pid_file)
        pid = int(pid_file.read_text())

        def cleanup() -> None:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)

        self.addCleanup(cleanup)
        return pid

    def cancel(self, process: subprocess.Popen[str]) -> None:
        """SIGTERM the runner and wait for its exit alone, before any pipe is drained."""
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)


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

    def test_every_spelling_of_a_scripts_path_selects_the_minimal_pins(self) -> None:
        spellings = [
            "scripts/test_x.py",
            "./scripts/test_x.py",
            ".//scripts//./test_x.py",
            f"{ROOT}/scripts/test_x.py",
            f"{os.environ.get('PWD', str(ROOT))}/scripts/test_x.py",
            f"{ROOT.resolve()}/scripts/test_x.py",
        ]
        for spelling in spellings:
            with self.subTest(spelling):
                self.stub_args.unlink(missing_ok=True)
                self.finish(self.local(spelling))
                line = self.stub_args.read_text().strip()
                self.assertIn("harness/test-requirements-min.txt", line)
                self.assertTrue(line.endswith("-s scripts -p test_x.py"), line)

    def test_a_skill_path_in_any_spelling_selects_the_full_pins(self) -> None:
        for spelling in ("skills/a/test_x.py", f"./skills/a/test_x.py", f"{ROOT}/skills/a/test_x.py"):
            with self.subTest(spelling):
                self.stub_args.unlink(missing_ok=True)
                self.finish(self.local(spelling))
                line = self.stub_args.read_text().strip()
                self.assertNotIn("-min", line)
                self.assertTrue(line.endswith("-s skills/a -p test_x.py"), line)


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
        self.stub_pid.unlink()
        process = self.local("pass/test_hang.py")
        run_dir = self.run_dir(process)
        pid = self.kill_at_cleanup(self.stub_pid)
        self.assertTrue(alive(pid))
        self.cancel(process)
        self.assertEqual(process.returncode, 143)
        self.assertEqual((run_dir / "done").read_text(), "rc=143\n")
        self.assertEqual((earlier / "done").read_text(), "rc=0\n")
        self.assertFalse(alive(pid), "the test process outlived the runner")

    def test_done_appears_only_after_a_term_ignoring_test_and_grandchild_are_gone(self) -> None:
        process = self.local("pass/test_stubborn.py")
        run_dir = self.run_dir(process)
        test_pid = self.kill_at_cleanup(self.stub_pid)
        grandchild = self.kill_at_cleanup(self.grandchild_pid)
        process.send_signal(signal.SIGTERM)
        self.wait_for(run_dir / "done")
        self.assertFalse(alive(grandchild), "done appeared while the grandchild lived")
        self.assertFalse(alive(test_pid), "done appeared while the test lived")
        process.wait(timeout=30)
        self.assertEqual((run_dir / "done").read_text(), "rc=143\n")

    def test_a_log_write_failure_after_a_passing_file_stops_the_run(self) -> None:
        run_dir, code, out, _ = self.finish(
            self.local("pass/test_breaklog.py", "pass/test_pass.py")
        )
        self.assertNotEqual(code, 0)
        done = (run_dir / "done").read_text()
        self.assertRegex(done, r"^rc=[1-9]\d*\n$")
        self.assertEqual(len(self.stub_args.read_text().splitlines()), 1, "a second test ran")
        self.assertNotIn("test_pass.py", out, "the run went on after its log failed")


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

    def test_sigterm_while_queued_stops_the_slot_before_the_runner_exits(self) -> None:
        process = self.slotted("pass/test_pass.py", FAKE_SLOT_MODE="queue")
        run_dir = self.run_dir(process)
        pid = self.kill_at_cleanup(self.slot_pid)
        self.cancel(process)
        self.assertEqual(process.returncode, 143)
        self.assertFalse(alive(pid), "the queued slot outlived the runner")
        self.assertEqual((run_dir / "done").read_text(), "rc=143\n")

    def test_cancelling_an_active_slot_waits_for_a_term_ignoring_grandchild(self) -> None:
        process = self.slotted("pass/test_stubborn.py")
        run_dir = self.run_dir(process)
        test_pid = self.kill_at_cleanup(self.stub_pid)
        grandchild = self.kill_at_cleanup(self.grandchild_pid)
        process.send_signal(signal.SIGTERM)
        self.wait_for(run_dir / "done")
        self.assertFalse(alive(grandchild), "done appeared while the grandchild lived")
        self.assertFalse(alive(test_pid), "done appeared while the test lived")
        process.wait(timeout=30)
        self.assertEqual((run_dir / "done").read_text(), "rc=143\n")

    def test_a_slot_that_exits_without_running_the_command_still_gets_done(self) -> None:
        run_dir, code, _, _ = self.finish(self.slotted("pass/test_pass.py", FAKE_SLOT_MODE="fail"))
        self.assertEqual(code, 1)
        self.assertEqual((run_dir / "done").read_text(), "rc=1\n")


class CheckoutTests(RunTestsCase):
    """The runner refuses a tree that is not the git checkout the caller meant."""

    def make_tree(self, with_git: bool) -> Path:
        tree = self.tmp / ("repo" if with_git else "synced")
        (tree / "harness").mkdir(parents=True)
        script = tree / "harness" / "run_tests.sh"
        script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        script.chmod(0o755)
        if with_git:
            git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "add", "."], check=True)
            subprocess.run([*git, "commit", "-qm", "init"], check=True)
        return tree

    def head(self, tree: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()

    def run_in(self, tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(tree / "harness" / "run_tests.sh"), "--run-root", str(self.root), *args, "pass/test_pass.py"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_a_copy_without_git_is_refused_before_a_run_directory_exists(self) -> None:
        result = self.run_in(self.make_tree(with_git=False))
        self.assertEqual(result.returncode, 2)
        self.assertIn("is not a git work tree (a synced copy without .git?)", result.stderr)
        self.assertTrue(result.stderr.startswith("run_tests: "))
        self.assertFalse(self.root.exists())

    def test_a_slot_run_checks_the_tree_before_it_waits_for_a_slot(self) -> None:
        tree = self.make_tree(with_git=False)
        result = subprocess.run(
            [str(tree / "harness" / "run_tests.sh"), "--slot", "--pkg", "A", "--item", "t1", "pass/test_pass.py"],
            env={**self.env, "FAKE_SLOT_MODE": "queue"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.home / "cad-bench" / "pkgA").exists())
        self.assertFalse(self.slot_pid.exists(), "the slot was reached")

    def test_a_matching_expect_head_passes_for_a_full_sha_and_a_prefix(self) -> None:
        tree = self.make_tree(with_git=True)
        sha = self.head(tree)
        for given in (sha, sha[:7], sha[:12].upper()):
            with self.subTest(given):
                result = self.run_in(tree, "--expect-head", given)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_mismatched_expect_head_is_refused_with_both_shas(self) -> None:
        tree = self.make_tree(with_git=True)
        sha = self.head(tree)
        wrong = ("0" if sha[0] != "0" else "1") + sha[1:12]
        result = self.run_in(tree, "--expect-head", wrong)
        self.assertEqual(result.returncode, 2)
        self.assertIn(sha, result.stderr)
        self.assertIn(wrong, result.stderr)
        self.assertFalse(self.root.exists())

    def test_a_prefix_shorter_than_seven_characters_is_a_usage_error(self) -> None:
        tree = self.make_tree(with_git=True)
        result = self.run_in(tree, "--expect-head", self.head(tree)[:6])
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr)

    def test_uncommitted_changes_to_tracked_files_warn_but_do_not_refuse(self) -> None:
        tree = self.make_tree(with_git=True)
        with (tree / "harness" / "run_tests.sh").open("a", encoding="utf-8") as script:
            script.write("# edited\n")
        result = self.run_in(tree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("uncommitted", result.stderr)

    def test_a_clean_tree_does_not_warn(self) -> None:
        result = self.run_in(self.make_tree(with_git=True))
        self.assertNotIn("uncommitted", result.stderr)


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
