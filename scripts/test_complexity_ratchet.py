# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Tests for the complexity ratchet, with ruff output injected."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import complexity_ratchet as cr  # noqa: E402

# Real ruff message shapes: only C901 names the function.
MESSAGES = {
    "C901": "`{name}` is too complex (11 > 10)",
    "PLR0912": "Too many branches (13 > 12)",
    "PLR0915": "Too many statements (51 > 50)",
}
SOURCE = """\
def alpha():
    pass


class Box:
    def method(self):
        def inner():
            pass


async def beta():
    pass
"""
# 1-based rows of the `def` lines in SOURCE.
ROWS = {"alpha": 1, "method": 6, "inner": 7, "beta": 11}


def fake_run(
    ruff: subprocess.CompletedProcess[str], git: subprocess.CompletedProcess[bytes] | None = None
):
    """subprocess.run stand-in: `git ls-files` gets `git` (default: one tracked file), ruff gets `ruff`."""
    listing = git or subprocess.CompletedProcess([], 0, stdout=b"a.py\0", stderr=b"")

    def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
        return listing if cmd[0] == "git" else ruff  # type: ignore[return-value]

    return run


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(root: Path) -> None:
    """A throwaway repo; identity is passed per command so no config is written."""
    git(root, "init", "-q")


class RatchetCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("a.py", "b.py", "c.py"):
            (self.root / name).write_text(SOURCE)
        self.baseline = self.root / "baseline.json"

    def finding(self, code: str, path: str, func: str) -> dict[str, object]:
        message = MESSAGES[code].format(name=func)
        row = ROWS[func]
        return {
            "code": code,
            "filename": str(self.root / path),
            "message": message,
            "location": {"row": row, "column": 5},
        }

    def write_baseline(self, findings: list[dict[str, object]]) -> None:
        self.baseline.write_text(
            json.dumps(cr.make_baseline(cr.collect(findings, self.root)))
        )

    def run_main(
        self, findings: list[dict[str, object]], *args: str
    ) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cr.main(
                ["--baseline", str(self.baseline), *args],
                run_ruff=lambda: findings,
                root=self.root,
            )
        return code, out.getvalue()


class RatchetTest(RatchetCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = [
            self.finding("C901", "a.py", "alpha"),
            self.finding("PLR0912", "a.py", "alpha"),
            self.finding("C901", "b.py", "beta"),
        ]
        self.extra = self.finding("C901", "c.py", "alpha")
        self.write_baseline(self.base)

    def test_rise_fails_and_names_new_offender(self) -> None:
        code, out = self.run_main([*self.base, self.extra])
        self.assertEqual(code, 1)
        self.assertIn("C901 c.py::alpha", out)
        self.assertNotIn("a.py::alpha", out)

    def test_equal_passes_without_update_hint(self) -> None:
        code, out = self.run_main(self.base)
        self.assertEqual(code, 0)
        self.assertNotIn("--update", out)

    def test_moved_function_with_equal_counts_passes(self) -> None:
        code, _ = self.run_main([self.finding("C901", "c.py", "beta"), *self.base[1:]])
        self.assertEqual(code, 0)

    def test_drop_passes_and_suggests_update(self) -> None:
        code, out = self.run_main(self.base[:2])
        self.assertEqual(code, 0)
        self.assertIn("--update", out)

    def test_update_lowers_baseline(self) -> None:
        code, _ = self.run_main(self.base[:2], "--update")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.baseline.read_text())["counts"]["C901"], 1)

    def test_update_refuses_to_raise(self) -> None:
        before = self.baseline.read_text()
        code, out = self.run_main([*self.base, self.extra], "--update")
        self.assertEqual(code, 1)
        self.assertEqual(self.baseline.read_text(), before)
        self.assertIn("--allow-increase", out)

    def test_allow_increase_rewrites_upward(self) -> None:
        code, _ = self.run_main(
            [*self.base, self.extra], "--update", "--allow-increase"
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.baseline.read_text())["counts"]["C901"], 3)


class PerRuleTest(RatchetCase):
    """A rise in one rule must fail even when another rule's drop keeps the total level."""

    def setUp(self) -> None:
        super().setUp()
        self.base = [
            self.finding("C901", "a.py", "alpha"),
            self.finding("PLR0912", "a.py", "alpha"),
            self.finding("PLR0915", "a.py", "alpha"),
        ]
        self.write_baseline(self.base)

    def swapped(self, rising: str) -> list[dict[str, object]]:
        """Baseline with one other rule dropped and `rising` gaining a second entry."""
        dropped = next(f for f in self.base if f["code"] != rising)
        kept = [f for f in self.base if f is not dropped]
        return [*kept, self.finding(rising, "b.py", "beta")]

    def test_rising_rule_fails_despite_equal_total(self) -> None:
        for rule in cr.RULES:
            with self.subTest(rule=rule):
                findings = self.swapped(rule)
                self.assertEqual(len(findings), len(self.base))
                code, out = self.run_main(findings)
                self.assertEqual(code, 1)
                self.assertIn(f"{rule} b.py::beta", out)

    def test_update_refuses_rising_rule_despite_equal_total(self) -> None:
        before = self.baseline.read_text()
        for rule in cr.RULES:
            with self.subTest(rule=rule):
                code, out = self.run_main(self.swapped(rule), "--update")
                self.assertEqual(code, 1)
                self.assertEqual(self.baseline.read_text(), before)
                self.assertIn("--allow-increase", out)


class FunctionNameTest(RatchetCase):
    def keys(self, *findings: dict[str, object]) -> set[str]:
        return set(cr.collect(list(findings), self.root))

    def test_branch_rule_resolves_enclosing_function_from_row(self) -> None:
        self.assertEqual(
            self.keys(
                self.finding("PLR0912", "a.py", "alpha"),
                self.finding("PLR0915", "a.py", "beta"),
            ),
            {"PLR0912 a.py::alpha", "PLR0915 a.py::beta"},
        )

    def test_same_rule_different_functions_in_one_file_stay_distinct(self) -> None:
        counts = cr.collect(
            [
                self.finding("PLR0912", "a.py", "alpha"),
                self.finding("PLR0912", "a.py", "beta"),
            ],
            self.root,
        )
        self.assertEqual(sum(counts.values()), 2)
        self.assertEqual(len(counts), 2)

    def test_methods_and_nested_defs_are_qualified(self) -> None:
        self.assertEqual(
            self.keys(
                self.finding("PLR0912", "a.py", "method"),
                self.finding("PLR0912", "a.py", "inner"),
            ),
            {"PLR0912 a.py::Box.method", "PLR0912 a.py::Box.method.inner"},
        )

    def test_row_inside_body_resolves_to_innermost_def(self) -> None:
        item = self.finding("PLR0915", "a.py", "alpha")
        item["location"] = {"row": 8, "column": 1}  # `pass` inside `inner`
        self.assertEqual(self.keys(item), {"PLR0915 a.py::Box.method.inner"})


class RuffBoundaryTest(RatchetCase):
    """What ruff prints, mocked at the subprocess call, can never read as zero findings."""

    def run_with_ruff(
        self,
        returncode: int,
        stdout: str,
        *args: str,
        stderr: str = "boom",
        git: subprocess.CompletedProcess[bytes] | None = None,
    ) -> int:
        self.baseline.write_text(
            json.dumps(
                {"counts": dict.fromkeys(cr.RULES, 1), "offenders": ["C901 a.py::f"]}
            )
        )
        before = self.baseline.read_text()
        proc = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)
        with (
            mock.patch.object(cr, "ruff_bin", return_value="ruff"),
            mock.patch.object(cr.subprocess, "run", side_effect=fake_run(proc, git)),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = cr.main(["--baseline", str(self.baseline), *args], root=self.root)
        self.assertEqual(self.baseline.read_text(), before)
        return code

    def test_blank_stdout_fails(self) -> None:
        for stdout in ("", "  \n"):
            for returncode in (0, 1):
                with self.subTest(stdout=stdout, returncode=returncode):
                    self.assertNotEqual(self.run_with_ruff(returncode, stdout), 0)
                    self.assertNotEqual(
                        self.run_with_ruff(returncode, stdout, "--update"), 0
                    )

    def test_abnormal_exit_fails(self) -> None:
        self.assertNotEqual(self.run_with_ruff(2, "[]"), 0)
        self.assertNotEqual(self.run_with_ruff(2, "[]", "--update"), 0)

    def test_malformed_json_fails(self) -> None:
        for stdout in ("{not json", '{"code": "C901"}', '["C901"]', "[{}]"):
            with self.subTest(stdout=stdout):
                self.assertNotEqual(self.run_with_ruff(1, stdout, "--update"), 0)

    def test_e902_io_error_diagnostic_fails_with_exit_2(self) -> None:
        diag = {
            "code": "E902",
            "filename": str(self.root / "a.py"),
            "message": "stream did not contain valid UTF-8",
            "location": {"row": 1, "column": 1},
        }
        self.assertEqual(self.run_with_ruff(1, json.dumps([diag])), 2)
        self.assertEqual(self.run_with_ruff(1, json.dumps([diag]), "--update"), 2)

    def test_io_error_warning_on_stderr_fails_with_exit_2(self) -> None:
        stderr = "warning: Failed to lint /repo/a.py: stream did not contain valid UTF-8\n"
        self.assertEqual(self.run_with_ruff(0, "[]", stderr=stderr), 2)
        self.assertEqual(self.run_with_ruff(0, "[]", "--update", stderr=stderr), 2)

    def test_diagnostic_with_bad_field_types_fails_with_exit_2(self) -> None:
        good = {"code": "C901", "filename": "/repo/a.py", "message": "m", "location": {"row": 1}}
        bad_fields = {
            "no location": {k: v for k, v in good.items() if k != "location"},
            "no row": {**good, "location": {"column": 1}},
            "row not int": {**good, "location": {"row": "1"}},
            "row bool": {**good, "location": {"row": True}},
            "location not dict": {**good, "location": [1]},
            "code not str": {**good, "code": 901},
            "filename not str": {**good, "filename": None},
            "message not str": {**good, "message": None},
        }
        for label, diag in bad_fields.items():
            with self.subTest(label):
                self.assertEqual(self.run_with_ruff(1, json.dumps([diag])), 2)
                self.assertEqual(self.run_with_ruff(1, json.dumps([diag]), "--update"), 2)

    def test_git_failure_or_empty_listing_fails_with_exit_2(self) -> None:
        cases = {
            "not a repository": subprocess.CompletedProcess([], 128, stdout=b"", stderr=b"fatal"),
            "empty list": subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
            "only separators": subprocess.CompletedProcess([], 0, stdout=b"\0\0", stderr=b""),
        }
        for label, listing in cases.items():
            with self.subTest(label):
                self.assertEqual(self.run_with_ruff(0, "[]", git=listing), 2)
                self.assertEqual(self.run_with_ruff(0, "[]", "--update", git=listing), 2)

    def run_real(self, *args: str) -> int:
        with (
            mock.patch.object(cr, "ruff_bin", return_value=shutil.which("ruff")),
            mock.patch.object(cr, "ROOT", self.root),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return cr.main(["--baseline", str(self.baseline), *args], root=self.root)

    @unittest.skipUnless(shutil.which("ruff") and shutil.which("git"), "needs ruff and git")
    def test_real_ruff_invalid_utf8_file_fails_with_exit_2(self) -> None:
        make_repo(self.root)
        (self.root / "bad.py").write_bytes(b'x = "\xff\xfe"\n')
        git(self.root, "add", "bad.py")
        self.baseline.write_text(json.dumps({"counts": dict.fromkeys(cr.RULES, 0), "offenders": []}))
        before = self.baseline.read_text()
        self.assertEqual(self.run_real("--update"), 2)
        self.assertEqual(self.baseline.read_text(), before)

    @unittest.skipUnless(shutil.which("ruff") and shutil.which("git"), "needs ruff and git")
    def test_real_ruff_counts_tracked_files_hidden_by_local_ignores(self) -> None:
        make_repo(self.root)
        branchy = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
        for name in ("excluded.py", "globally_ignored.py"):
            (self.root / name).write_text(branchy)
        git(self.root, "add", "excluded.py", "globally_ignored.py")
        (self.root / ".git" / "info" / "exclude").write_text("excluded.py\n")
        global_ignore = self.root / "global-ignore"
        global_ignore.write_text("globally_ignored.py\n")
        git(self.root, "config", "core.excludesFile", str(global_ignore))  # temp repo only
        self.assertEqual(self.run_real("--update", "--allow-increase"), 0)
        offenders = json.loads(self.baseline.read_text())["offenders"]
        self.assertEqual(offenders, ["C901 excluded.py::f", "C901 globally_ignored.py::f"])

    def test_genuine_empty_list_is_zero_findings(self) -> None:
        self.baseline.write_text(
            json.dumps({"counts": dict.fromkeys(cr.RULES, 0), "offenders": []})
        )
        proc = subprocess.CompletedProcess([], 0, stdout="[]\n", stderr="")
        with (
            mock.patch.object(cr, "ruff_bin", return_value="ruff"),
            mock.patch.object(cr.subprocess, "run", side_effect=fake_run(proc)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                cr.main(["--baseline", str(self.baseline)], root=self.root), 0
            )


if __name__ == "__main__":
    unittest.main()
