# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""Guard: no tracked test or script may skip git hooks with --no-verify or a short -n commit flag.

A test repo that must not run host hooks sets ``core.hooksPath`` to an empty directory instead.

Scanned: every tracked Python file under a ``scripts/`` or ``tests/`` directory at any depth, under
``harness/`` or ``.github/``, and every ``test_*`` file, plus every ``run:`` value in the YAML files under
``.github/``. The repo tracks no shell scripts, and the guard has no shell lexer.

Python files are read with ``ast``. A string constant equal to ``--no-verify`` fails, wherever it sits
outside a docstring. A short cluster (a constant matching ``-[A-Za-z]*n[A-Za-z]*``, such as ``-n`` or
``-qn``) fails when the same function, or the module top level, also holds the constant ``commit``. A
cluster in a function that holds ``push`` and no ``commit`` passes: there ``-n`` means ``--dry-run``.

Workflow files are read with PyYAML, and each ``run:`` value is judged as plain text by two coarse rules:
1. The value contains ``--no-verify`` anywhere, comments included.
2. The value contains the word ``commit`` and a short cluster such as ``-n`` or ``-qn`` anywhere. The cluster must
   not touch a letter, digit, underscore or hyphen, so any other character bounds it: ``-n>/dev/null``,
   ``-qn;true`` and ``-n)`` all match.
The rules over-flag on purpose, for example ``true;# commit -n``, so that no shell parsing is needed. A YAML
file that does not parse fails, naming the file.

A real non-git use that rule 2 catches takes the marker ``# no-verify-guard: not git`` in the same Python
line or anywhere in the run value. Only rule 2 honours it. The guard writes every marker it finds to stderr,
and ``EXPECTED_MARKERS`` pins how many the repo holds, so a new marker shows in review.

Known limits (follow-up issue #269). The Python check reads string constants, so it cannot see:
- a flag built at runtime, by f-string interpolation or by ``+``;
- a short cluster passed into a helper defined in another function, such as the ``commit`` helper in
  ``skills/cad-recheck/tests/test_recheck.py``, which holds ``commit`` while its callers hold the cluster.
"""

import ast
import io
import re
import subprocess
import sys
import tokenize
import unittest
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCANNED = re.compile(r"^(harness|\.github)/|(^|/)(scripts|tests)/|(^|/)test_[^/]*$")
WORKFLOWS = re.compile(r"^\.github/.*\.ya?ml$")
LONG_FLAG = "--no-" + "verify"
SUBCOMMAND = re.compile(r"\bcommit\b")
SUBCOMMAND_CONSTANT = "commit"
CLUSTER = re.compile(r"^-[A-Za-z]*n[A-Za-z]*$")
WORKFLOW_CLUSTER = re.compile(r"(?<![A-Za-z0-9_-])-[A-Za-z]*n[A-Za-z]*(?![A-Za-z0-9_-])")
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
        has_commit = any(value == SUBCOMMAND_CONSTANT for value, _ in strings)
        for value, line in strings:
            if value == LONG_FLAG or (has_commit and CLUSTER.match(value) and line not in marked):
                found.add(line)
    return sorted(found)


def run_values(text: str) -> list[tuple[int, str]]:
    """(line of the first text row, value) for every ``run:`` scalar in a workflow. Raises yaml.YAMLError on a parse failure."""
    found: list[tuple[int, str]] = []

    def walk(node: yaml.Node) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.value == "run" and isinstance(value, yaml.ScalarNode):
                    first_row = value.start_mark.line + 1 + (value.style in ("|", ">"))
                    found.append((first_row, value.value))
                walk(value)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item)

    for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
        if document is not None:
            walk(document)
    return found


def run_breaks_rules(value: str) -> bool:
    if LONG_FLAG in value:
        return True
    if MARKER.search(value) or not SUBCOMMAND.search(value):
        return False
    return WORKFLOW_CLUSTER.search(value) is not None


def workflow_offences(path: str, text: str) -> list[str]:
    try:
        values = run_values(text)
    except yaml.YAMLError as error:
        return [f"{path}: YAML does not parse: {str(error).splitlines()[0]}"]
    return [f"{path}:{line}: {value.strip().splitlines()[0] if value.strip() else ''}" for line, value in values if run_breaks_rules(value)]


def marker_lines(path: str, text: str) -> list[int]:
    if path.endswith(".py"):
        return python_marker_lines(text)
    if not WORKFLOWS.match(path):
        return []
    try:
        values = run_values(text)
    except yaml.YAMLError:
        return []
    return [line + offset for line, value in values for offset, row in enumerate(value.splitlines()) if MARKER.search(row)]


def offences(path: str, text: str) -> list[str]:
    if path.endswith(".py"):
        try:
            lines = python_offences(text)
        except SyntaxError as error:
            return [f"{path}: Python does not parse: {error.msg}"]
        source = text.splitlines()
        return [f"{path}:{n}: {source[n - 1].strip() if n <= len(source) else ''}" for n in lines]
    if WORKFLOWS.match(path):
        return workflow_offences(path, text)
    return []


def tracked_texts() -> list[tuple[str, str]]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    chosen = [path for path in filter(None, listing) if selected(path) and (ROOT / path).is_file()]
    return [(path, (ROOT / path).read_text(encoding="utf-8", errors="replace")) for path in chosen]


def wf(run: str) -> str:
    """A workflow with one step whose ``run`` is the given YAML scalar text."""
    return f"jobs:\n  j:\n    steps:\n      - run: {run}\n"


def wf_block(*lines: str) -> str:
    """A workflow with one step whose ``run`` is a literal block of the given lines."""
    body = "".join(f"          {line}\n" for line in lines)
    return f"jobs:\n  j:\n    steps:\n      - run: |\n{body}"


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
        self.assertEqual(marker_lines("a.py", 'X = "# no-verify-guard: not git"\n'), [])

    def test_python_flags_the_call_shapes_of_the_recheck_helpers(self) -> None:
        cases = {
            "method call long flag": 'def f(self):\n    self.git("commit", "-q", "--no-verify", "-m", "x")',
            "method call cluster": 'def f(self):\n    self.git("commit", "-qn", "-m", "x")',
            "run wrapper": 'def f(repo):\n    run(repo, "commit", "-n")',
        }
        for name, source in cases.items():
            with self.subTest(name):
                self.assertEqual(len(python_offences(source)), 1)


class WorkflowRunTests(unittest.TestCase):
    """Workflow ``run:`` values are read with the YAML parser and judged by two coarse token rules."""

    PATH = ".github/workflows/ci.yml"

    def found(self, text: str) -> int:
        return len(offences(self.PATH, text))

    def test_the_long_flag_anywhere_in_a_run_value_fails(self) -> None:
        for name, text in {
            "plain scalar": wf("git push --no-verify origin main"),
            "embedded hash before the flag": wf("git commit -m fix#263 --no-verify"),
            "quoted whole command": wf('"git commit -m x --no-verify"'),
            "quoted flag in a multiline value": wf_block('git commit "--no-verify" -m "first', 'second"'),
            "quoted hash in a message, then the flag": wf_block("git commit -m 'fix #263' --no-verify"),
            "embedded hash, then a quoted hash": wf_block("git commit -m fix#1 --no-verify -m '#'"),
            "two messages, the second quoted with a hash": wf_block("git commit -m fix#1 -m 'fix #2' --no-verify"),
            "inside a comment": wf_block("git commit -m x # --no-verify"),
        }.items():
            with self.subTest(name):
                self.assertEqual(self.found(text), 1)

    def test_a_short_cluster_beside_commit_fails(self) -> None:
        for name, text in {
            "plain": wf("git commit -qn -m x"),
            "embedded hash before the cluster": wf("git commit -m fix#263 -n"),
            "quoted whole command": wf('"git commit -m x -n"'),
            "quoted token": wf_block("git commit '-n' -m x"),
            "quoted hash in a message, then the cluster": wf_block("git commit -m 'fix #263' -n"),
            "continued lines": wf_block("git commit \\", "  -qn \\", "  -m x"),
        }.items():
            with self.subTest(name):
                self.assertEqual(self.found(text), 1)

    def test_a_short_cluster_glued_to_a_shell_separator_fails(self) -> None:
        for name, text in {
            "redirect": wf("git commit -n>/dev/null -m x"),
            "and list": wf("git commit -n&&echo done"),
            "semicolon": wf("git commit -qn;true"),
            "pipe": wf("git commit -n|cat"),
            "semicolon then a word": wf("git commit -qn;echo ok"),
            "line continuation": wf_block("git commit -qn\\", "  -m x"),
            "command substitution in quotes": wf_block('echo "$(git commit -m fix -n)."'),
            "subshell": wf_block("(git commit -n)"),
            "backticks": wf_block("`git commit -qn`"),
            "input redirect": wf("git commit -n</dev/null -m x"),
        }.items():
            with self.subTest(name):
                self.assertEqual(self.found(text), 1)

    def test_a_comment_hash_glued_to_a_command_is_over_flagged_by_design(self) -> None:
        """#273 listed ``true;# commit -n`` as a false positive. Rule 2 reads tokens, not shell comments, so it now fails."""
        self.assertEqual(self.found(wf_block("true;# commit -n")), 1)

    def test_a_run_value_without_commit_passes_rule_2(self) -> None:
        for name, text in {
            "push dry run": wf("git push -n"),
            "grep -n": wf("grep -n foo file"),
            "plain commit": wf("git commit -qm x"),
            "no run key": "name: ci\non: push\n",
        }.items():
            with self.subTest(name):
                self.assertEqual(self.found(text), 0)

    def test_text_outside_run_values_is_not_read(self) -> None:
        self.assertEqual(self.found("# git commit -n --no-verify\njobs:\n  j:\n    name: commit -n\n    steps: []\n"), 0)

    def test_the_marker_exempts_rule_2_only(self) -> None:
        self.assertEqual(self.found(wf_block("grep -n commit f # no-verify-guard: not git")), 0)
        self.assertEqual(self.found(wf_block("git commit --no-verify # no-verify-guard: not git")), 1)
        self.assertEqual(marker_lines(self.PATH, wf_block("echo ok", "grep -n commit f # no-verify-guard: not git")), [6])

    def test_malformed_yaml_fails_and_names_the_file(self) -> None:
        found = offences(self.PATH, "jobs: [unclosed\n")
        self.assertEqual(len(found), 1)
        self.assertIn(self.PATH, found[0])

    def test_the_offence_names_the_line_of_the_run_value(self) -> None:
        found = offences(self.PATH, wf("echo ok") + "      - run: git push --no-verify\n")
        self.assertTrue(found[0].startswith(self.PATH + ":5:"), found)


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
