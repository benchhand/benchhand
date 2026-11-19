# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard: no tracked test or script may skip git hooks with --no-verify or a short -n commit flag.

A test repo that must not run host hooks sets ``core.hooksPath`` to an empty directory instead.

Scanned: every tracked file under a ``scripts/`` or ``tests/`` directory at any depth, under ``harness/``
or ``.github/``, and every ``test_*`` file. Markdown is skipped, so docs may quote the flag.

What counts as a bypass is read from a whole ``git`` invocation, not from one line:
- in Python, the stdlib ``ast`` finds calls named ``git`` (a test helper) and list literals that start with
  ``"git"``; the first non-option word is the subcommand. Strings and docstrings are never read, so the
  guard needs no exemption for its own fixtures;
- in shell, YAML and similar text, backslash-continued lines are joined and each ``git`` command is split
  with ``shlex``.
``--no-verify`` is flagged on ``commit`` and ``push``. A short cluster holding ``n`` is flagged on ``commit``
only, where it means ``--no-verify``; on ``push`` it means ``--dry-run``.
"""

import ast
import re
import shlex
import subprocess
import unittest
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = re.compile(r"^(harness|\.github)/|(^|/)(scripts|tests)/|(^|/)test_[^/]*$")
TEXT_SUFFIXES = {".sh", ".bash", ".zsh", ".yml", ".yaml", ".mk", ""}
GLOBAL_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
COMMIT_ARG_LETTERS = set("mFCcSu")  # a short option that takes the rest of the cluster, or the next word, as its value
COMMIT_OPTIONS_WITH_VALUE = {"-m", "-F", "-C", "-c", "--message", "--file", "--author", "--date", "--template"}


def selected(path: str) -> bool:
    return not path.endswith(".md") and bool(SCANNED.search(path))


def bypasses(words: Sequence[str | None]) -> bool:
    """True when the words after ``git`` are a commit or push that skips hooks."""
    index = 0
    while index < len(words):
        word = words[index]
        if word is None or not word.startswith("-"):
            break
        index += 2 if word in GLOBAL_OPTIONS_WITH_VALUE else 1
    if index >= len(words) or words[index] not in ("commit", "push"):
        return False
    commit = words[index] == "commit"
    skip_value = False
    for word in words[index + 1 :]:
        if skip_value:
            skip_value = False
            continue
        if word is None or not word.startswith("-"):
            continue
        if word == "--no-verify":
            return True
        if word in COMMIT_OPTIONS_WITH_VALUE:
            skip_value = True
        elif commit and not word.startswith("--"):
            for letter in word[1:]:
                if letter == "n":
                    return True
                if letter in COMMIT_ARG_LETTERS:
                    skip_value = letter == word[-1]
                    break
    return False


def constant_words(nodes: list[ast.expr]) -> list[str | None]:
    return [n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None for n in nodes]


def python_offences(source: str) -> list[int]:
    """Line numbers of the git calls and git argument lists in Python source that skip hooks."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        words = None
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name == "git":
                words = constant_words(node.args)
        elif isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = constant_words(node.elts[:1])[0]
            if first == "git":
                words = constant_words(node.elts[1:])
        if words is not None and bypasses(words):
            found.add(node.lineno)
    return sorted(found)


def logical_lines(text: str) -> list[tuple[int, str]]:
    """(first physical line number, text) for each line, with backslash continuations joined."""
    lines: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        start, joined = pending if pending else (number, "")
        joined += line
        if joined.endswith("\\"):
            pending = (start, joined[:-1] + " ")
        else:
            pending = None
            lines.append((start, joined))
    if pending:
        lines.append(pending)
    return lines


def text_offences(text: str) -> list[int]:
    """Line numbers of the git commands in shell or YAML text that skip hooks."""
    found = []
    for number, line in logical_lines(text):
        if line.lstrip().startswith("#") or "git" not in line:
            continue
        for part in re.split(r"[;&|]+", line):
            try:
                words = shlex.split(part, comments=True)
            except ValueError:
                words = part.split()
            if any(word == "git" and bypasses(words[at + 1 :]) for at, word in enumerate(words)):
                found.append(number)
    return found


def offences(path: str, text: str) -> list[str]:
    suffix = Path(path).suffix
    try:
        if suffix == ".py":
            lines = python_offences(text)
        elif suffix in TEXT_SUFFIXES:
            lines = text_offences(text)
        else:
            return []
    except SyntaxError:
        lines = text_offences(text)
    source = text.splitlines()
    return [f"{path}:{n}: {source[n - 1].strip() if n <= len(source) else ''}" for n in lines]


class SelectionTests(unittest.TestCase):
    def test_selects_scripts_tests_harness_and_ci_at_any_depth(self) -> None:
        chosen = [
            "scripts/ci_shards.py",
            "skills/cad-recheck/scripts/stamp.py",
            "skills/cad-model/tests/test_cad_model.py",
            "skills/cad-bom/test_bom.py",
            "harness/install.sh",
            ".github/workflows/ci.yml",
        ]
        skipped = ["README.md", "docs/scripts/notes.md", "skills/cad-recheck/SKILL.md", "skills/cad-model/lib/runner.py"]
        for path in chosen:
            self.assertTrue(selected(path), path)
        for path in skipped:
            self.assertFalse(selected(path), path)


class PythonDetectorTests(unittest.TestCase):
    def test_flags_bypass_inside_a_git_call_however_it_is_written(self) -> None:
        cases = {
            "helper call": 'self.git("commit", "-qm", "x", "--no-verify")',
            "wrapped args": 'self.git(\n    "commit",\n    "-qn",\n    "-m", "x",\n)',
            "cluster with n last": 'subprocess.run(["git", "commit", "-qn", "-m", "x"])',
            "cluster with n first": 'subprocess.run(["git", "commit", "-nq", "-m", "x"])',
            "cluster before message": 'subprocess.run(["git", "commit", "-q", "-nm", "x"])',
            "global options first": 'git("-C", "repo", "commit", "-n", "-m", "x")',
            "push": 'subprocess.run(["git", "push", "--no-verify"])',
        }
        for name, source in cases.items():
            self.assertTrue(python_offences(source), name)

    def test_ignores_what_is_not_a_hook_bypass(self) -> None:
        cases = {
            "plain commit": 'self.git("commit", "-qm", "x")',
            "grep -n": 'subprocess.run(["grep", "-n", "commit", "notes.txt"])',
            "docstring quoting the rule": '"""Run git commit -n only in a hook test."""',
            "-n as a message": 'self.git("commit", "-m", "-n")',
            "push dry run": 'subprocess.run(["git", "push", "-n"])',
            "log -n": 'subprocess.run(["git", "log", "-n", "3"])',
            "unrelated -n beside a commit comment": 'x = ["grep", "-n", "y"]  # commit later',
        }
        for name, source in cases.items():
            self.assertEqual(python_offences(source), [], name)


class TextDetectorTests(unittest.TestCase):
    def test_reports_the_first_physical_line_of_a_continued_command(self) -> None:
        self.assertEqual(text_offences("echo ok\ngit commit \\\n  -qn \\\n  -m x\necho done\n"), [2])

    def test_flags_bypass_in_shell_and_yaml_text(self) -> None:
        cases = {
            "short flag": "git commit -n -m x",
            "continued lines": "git commit \\\n  -qn \\\n  -m x",
            "push": "git push --no-verify origin main",
            "global options": "run: git -C d commit -nm x",
        }
        for name, text in cases.items():
            self.assertTrue(text_offences(text), name)

    def test_ignores_what_is_not_a_hook_bypass(self) -> None:
        cases = {
            "grep -n": "grep -n commit notes.txt",
            "comment": "# git commit -n is banned\necho ok",
            "-n inside a message": "git commit -m 'drop -n'",
            "log -n": "git log -n 3",
            "grep -n beside commit comment": "grep -n x f  # commit later",
        }
        for name, text in cases.items():
            self.assertEqual(text_offences(text), [], name)


class NoVerifyGuardTests(unittest.TestCase):
    def test_no_tracked_test_or_script_bypasses_hooks(self) -> None:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split("\0")
        found: list[str] = []
        for path in filter(None, listing):
            file = ROOT / path
            if selected(path) and file.is_file():
                found += offences(path, file.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(found, [], "use core.hooksPath on an empty dir, not a bypass flag")


if __name__ == "__main__":
    unittest.main()
