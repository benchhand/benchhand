# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard: no tracked test or script may skip git hooks with --no-verify or a short -n commit flag.

A test repo that must not run host hooks sets ``core.hooksPath`` to an empty directory instead.

Scanned: every tracked file under a ``scripts/`` or ``tests/`` directory at any depth, under ``harness/``
or ``.github/``, and every ``test_*`` file. Markdown is skipped, so docs may quote the flag.

The guard does not try to recognise git invocations; a wrapper, a helper that takes the repo first, an argv
built in a variable or a splat would all escape that. It applies two coarse rules instead:
1. The long flag. A Python string constant equal to ``--no-verify`` fails, wherever it sits. In shell and
   YAML text, a ``--no-verify`` word outside a comment fails. Docstrings are not read.
2. A short cluster. A Python string constant that matches ``-[A-Za-z]*n[A-Za-z]*`` (``-n``, ``-qn``, ``-nq``)
   fails when the same function, or the module top level, also holds the string constant ``commit``. In
   shell text, the same cluster and the word ``commit`` on one joined command line fail. A cluster in a
   function that holds ``push`` and no ``commit`` passes: there ``-n`` means ``--dry-run``.

A real non-git use that rule 2 catches takes the same-line comment ``# no-verify-guard: not git``. Only
rule 2 honours it. The guard writes every marker it finds to stderr, and ``EXPECTED_MARKERS`` pins how many
the repo holds, so a new marker shows in review.

Known limits. The guard reads string constants, so it cannot see:
- a flag built at runtime, by f-string interpolation or by ``+``;
- a short cluster passed into a helper defined in another function, such as the ``commit`` helper in
  ``skills/cad-recheck/tests/test_recheck.py``, which holds ``commit`` while its callers hold the cluster.
Both are tracked in a follow-up issue (number to be added).
"""

import ast
import io
import re
import shlex
import subprocess
import sys
import tokenize
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = re.compile(r"^(harness|\.github)/|(^|/)(scripts|tests)/|(^|/)test_[^/]*$")
TEXT_SUFFIXES = {".sh", ".bash", ".zsh", ".yml", ".yaml", ".mk", ""}
LONG_FLAG = "--no-" + "verify"
SUBCOMMAND = "commit"
CLUSTER = re.compile(r"^-[A-Za-z]*n[A-Za-z]*$")
MARKER = re.compile(r"#\s*no-verify-guard: not git")
EXPECTED_MARKERS = 0


def selected(path: str) -> bool:
    return not path.endswith(".md") and bool(SCANNED.search(path))


def docstring_ids(tree: ast.AST) -> set[int]:
    """Identity of each module, class and function docstring constant."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


class ScopeStrings(ast.NodeVisitor):
    """String constants (value, line) grouped by the innermost enclosing function; the module is scope 0."""

    def __init__(self, skip: set[int]) -> None:
        self.skip = skip
        self.stack = [0]
        self.scopes: dict[int, list[tuple[str, int]]] = defaultdict(list)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(id(node))
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.skip:
            self.scopes[self.stack[-1]].append((node.value, node.lineno))


def python_marker_lines(source: str) -> list[int]:
    lines = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and MARKER.search(token.string):
                lines.append(token.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    return lines


def python_offences(source: str) -> list[int]:
    """Line numbers of the string constants that break rule 1 or rule 2."""
    tree = ast.parse(source)
    marked = set(python_marker_lines(source))
    collector = ScopeStrings(docstring_ids(tree))
    collector.visit(tree)
    found = set()
    for strings in collector.scopes.values():
        has_commit = any(value == SUBCOMMAND for value, _ in strings)
        for value, line in strings:
            if value == LONG_FLAG or (has_commit and CLUSTER.match(value) and line not in marked):
                found.add(line)
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


def shell_words(line: str) -> list[str]:
    """Words of one joined command line. shlex drops a comment but keeps a # inside quotes; an unbalanced quote falls back to a plain split."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return list(lexer)
    except ValueError:
        return line.split()


def text_offences(text: str) -> list[int]:
    """Line numbers of the joined shell or YAML command lines that break rule 1 or rule 2."""
    found = []
    for number, line in logical_lines(text):
        words = shell_words(line)
        bare_cluster = any(CLUSTER.match(word) for word in words) and SUBCOMMAND in words
        if LONG_FLAG in words or (bare_cluster and not MARKER.search(line)):
            found.append(number)
    return found


def marker_lines(path: str, text: str) -> list[int]:
    if path.endswith(".py"):
        return python_marker_lines(text)
    return [number for number, line in logical_lines(text) if MARKER.search(line)]


def offences(path: str, text: str) -> list[str]:
    suffix = Path(path).suffix
    if suffix == ".py":
        try:
            lines = python_offences(text)
        except SyntaxError:
            lines = text_offences(text)
    elif suffix in TEXT_SUFFIXES:
        lines = text_offences(text)
    else:
        return []
    source = text.splitlines()
    return [f"{path}:{n}: {source[n - 1].strip() if n <= len(source) else ''}" for n in lines]


def tracked_texts() -> list[tuple[str, str]]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    chosen = [path for path in filter(None, listing) if selected(path) and (ROOT / path).is_file()]
    return [(path, (ROOT / path).read_text(encoding="utf-8", errors="replace")) for path in chosen]


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


class CoarseRuleTests(unittest.TestCase):
    def test_python_flags_the_long_flag_wherever_the_constant_sits(self) -> None:
        cases = {
            "git helper": 'self.git("commit", "-qm", "x", "--no-verify")',
            "other wrapper name": 'run("git", "commit", "--no-verify", cwd=repo)',
            "context first helper": 'git(self.root, "commit", "-q", "--no-verify")',
            "argv in a variable": 'def f():\n    argv = ["commit", "--no-verify"]\n    go(argv)',
            "list starting at the subcommand": 'ARGS = ["push", "--no-verify"]',
            "module level": 'FLAG = "--no-verify"',
        }
        for name, source in cases.items():
            self.assertTrue(python_offences(source), name)

    def test_python_flags_a_short_cluster_beside_commit_in_the_same_function(self) -> None:
        cases = {
            "literal call": 'def f():\n    git("commit", "-qn", "-m", "x")',
            "variable and splat": 'def f():\n    args = ["-qn", "-m", "x"]\n    self_git("commit", *args)',
            "cluster first": 'def f():\n    go("-nq", "commit")',
            "bare -n": 'def f():\n    go("commit", "-n")',
            "nested helper sees its own scope": 'def f():\n    def g():\n        return ["commit", "-qn"]',
            "module top level": 'ARGS = ["commit", "-qn"]',
        }
        for name, source in cases.items():
            self.assertEqual(len(python_offences(source)), 1, name)

    def test_python_passes_what_is_not_a_bypass(self) -> None:
        cases = {
            "plain commit": 'def f():\n    git("commit", "-qm", "x")',
            "push dry run": 'def f():\n    git("push", "-n")',
            "grep -n without commit": 'def f():\n    go("grep", "-n", "x")',
            "cluster and commit in different functions": 'def f():\n    go("commit")\n\ndef g():\n    go("-n")',
            "docstring quoting both": 'def f():\n    """git commit -n and --no-verify"""\n    return 1',
            "module docstring": '"""Never pass --no-verify."""\nX = 1',
            "long flag inside a longer string": 'MSG = "do not pass --no-verify here"',
            "double dash cluster": 'def f():\n    go("commit", "--dry-run")',
            "marker on the line": 'def f():\n    go("commit", "-n")  # no-verify-guard: not git',
        }
        for name, source in cases.items():
            self.assertEqual(python_offences(source), [], name)

    def test_the_marker_exempts_only_its_own_line(self) -> None:
        source = 'def f():\n    go("commit", "-n")  # no-verify-guard: not git\n    go("-qn")\n'
        self.assertEqual(python_offences(source), [3])
        self.assertEqual(marker_lines("a.py", source), [2])
        self.assertEqual(marker_lines("a.sh", "grep -n x # no-verify-guard: not git\n"), [1])
        self.assertEqual(marker_lines("a.py", 'X = "# no-verify-guard: not git"\n'), [])

    def test_text_flags_the_long_flag_and_short_clusters_per_command_line(self) -> None:
        for name, text in {
            "long flag": "git push --no-verify origin main",
            "long flag in yaml": "run: git commit -m x --no-verify",
            "cluster beside commit": "git commit -qn -m x",
            "continued lines": "git commit \\\n  -qn \\\n  -m x",
            "cluster split by quotes": "git commit '-n' -m x",
        }.items():
            self.assertTrue(text_offences(text), name)
        for name, text in {
            "comment": "# git commit -n\necho ok",
            "trailing comment": "git commit -m x # --no-verify",
            "grep -n alone": "grep -n x file",
            "push dry run": "git push -n",
            "marker": "grep -n commit f # no-verify-guard: not git",
        }.items():
            self.assertEqual(text_offences(text), [], name)

    def test_a_hash_inside_quotes_is_not_a_comment(self) -> None:
        for name, text in {
            "long flag after a quoted hash": "git commit -m 'fix #263' --no-verify",
            "short cluster after a quoted hash": "git commit -m 'fix #263' -n",
        }.items():
            with self.subTest(name):
                self.assertEqual(text_offences(text), [1])

    def test_reports_the_first_physical_line_of_a_wrapped_command(self) -> None:
        self.assertEqual(text_offences("echo ok\ngit commit \\\n  -qn \\\n  -m x\necho done\n"), [2])


class NoVerifyGuardTests(unittest.TestCase):
    def test_no_tracked_test_or_script_bypasses_hooks(self) -> None:
        found: list[str] = []
        honoured: list[str] = []
        for path, text in tracked_texts():
            found += offences(path, text)
            honoured += [f"{path}:{n}" for n in marker_lines(path, text)]
        sys.stderr.write("no-verify-guard markers honoured: " + (", ".join(honoured) or "none") + "\n")
        self.assertEqual(found, [], "use core.hooksPath on an empty dir, not a bypass flag")

    def test_the_repo_holds_exactly_the_pinned_markers(self) -> None:
        honoured = [f"{p}:{n}" for p, t in tracked_texts() for n in marker_lines(p, t)]
        self.assertEqual(len(honoured), EXPECTED_MARKERS, honoured)


if __name__ == "__main__":
    unittest.main()
